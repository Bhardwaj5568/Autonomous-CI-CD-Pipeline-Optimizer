from sqlalchemy.orm import Session
from app.models import RecommendationFeedback
from collections import Counter

def store_feedback(db: Session, run_id: str, vote: str, comment: str, actor: str):
    db.add(RecommendationFeedback(run_id=run_id, vote=vote, comment=comment, actor=actor))
    db.commit()

def get_feedback_stats(db: Session, min_count: int = 3):
    # Aggregate feedback for each job/step
    feedback = db.query(RecommendationFeedback).all()
    job_votes = {}
    for fb in feedback:
        # Assume comment contains job/step name for simplicity
        job = fb.comment.strip().lower()
        if not job:
            continue
        if job not in job_votes:
            job_votes[job] = []
        job_votes[job].append(fb.vote)
    # Find jobs with consistent negative feedback
    auto_remove = []
    for job, votes in job_votes.items():
        if len(votes) >= min_count:
            vote_counts = Counter(votes)
            if vote_counts.get("remove", 0) / len(votes) > 0.7:
                auto_remove.append(job)
    return auto_remove

def adjust_optimization_from_feedback(db: Session, yaml_content: str) -> str:
    # Remove jobs/steps with strong negative feedback
    auto_remove = get_feedback_stats(db)
    if not auto_remove:
        return yaml_content
    import yaml
    data = yaml.safe_load(yaml_content)
    jobs = data.get("jobs", {})
    for job in auto_remove:
        if job in jobs:
            del jobs[job]
    data["jobs"] = jobs
    return yaml.dump(data)
