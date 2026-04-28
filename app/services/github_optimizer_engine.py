import yaml
from typing import List

def detect_redundant_steps(events: list[dict]) -> List[str]:
    job_stats = {}
    for e in events:
        job = e.get("stage_name")
        if job not in job_stats:
            job_stats[job] = {"count": 0, "skipped": 0, "total_duration": 0}
        job_stats[job]["count"] += 1
        if e.get("status") == "skipped":
            job_stats[job]["skipped"] += 1
        job_stats[job]["total_duration"] += int(e.get("duration_ms") or 0)
    redundant = []
    for job, stats in job_stats.items():
        if stats["skipped"] == stats["count"] or stats["total_duration"] < 1000:
            redundant.append(job)
    return redundant

def remove_steps_from_yaml(yaml_content: str, steps_to_remove: List[str]) -> str:
    data = yaml.safe_load(yaml_content)
    jobs = data.get("jobs", {})
    for job in steps_to_remove:
        if job in jobs:
            del jobs[job]
    data["jobs"] = jobs
    return yaml.dump(data)

def detect_slow_steps(events: list[dict], threshold_ms=5000) -> List[str]:
    slow = []
    for e in events:
        if int(e.get("duration_ms", 0)) > threshold_ms:
            slow.append(e.get("stage_name"))
    return slow

def enable_parallel_for_steps(yaml_content: str, steps: List[str]) -> str:
    data = yaml.safe_load(yaml_content)
    for job in steps:
        if job in data.get("jobs", {}):
            data["jobs"][job]["strategy"] = {"matrix": {"os": ["ubuntu-latest", "windows-latest"]}}
    return yaml.dump(data)
