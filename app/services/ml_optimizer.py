"""
ML Optimizer — Learns from actual build history stored in DB.
No manual feedback required. Uses statistical patterns from PipelineEvent data.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PipelineEvent, RecommendationFeedback


# ---------------------------------------------------------------------------
# Learn from build history (primary signal)
# ---------------------------------------------------------------------------

def learn_step_patterns(db: Session, repository_id: str | None = None, min_runs: int = 5) -> dict[str, dict]:
    """
    Mine patterns from PipelineEvent history.
    Returns per-step stats with ML-derived labels.
    """
    query = select(PipelineEvent)
    if repository_id:
        query = query.where(PipelineEvent.repository_id == repository_id)
    query = query.order_by(PipelineEvent.created_at.desc()).limit(5000)

    rows = db.execute(query).scalars().all()
    if not rows:
        return {}

    step_data: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.stage_name:
            step_data[r.stage_name].append({
                "status": r.status,
                "duration_ms": r.duration_ms,
                "retry_count": r.retry_count,
                "created_at": r.created_at,
            })

    patterns = {}
    for step, history in step_data.items():
        if len(history) < min_runs:
            continue

        total = len(history)
        failed = sum(1 for h in history if h["status"] in {"failed", "failure"})
        skipped = sum(1 for h in history if h["status"] == "skipped")
        durations = [h["duration_ms"] for h in history if h["duration_ms"] > 0]
        retries = sum(h["retry_count"] for h in history)

        fail_rate = failed / total
        skip_rate = skipped / total
        avg_dur = mean(durations) if durations else 0
        avg_retries = retries / total

        # Duration trend using numpy linear regression
        dur_trend_slope = 0.0
        if len(durations) >= 4:
            x = np.arange(len(durations), dtype=float)
            y = np.array(durations, dtype=float)
            coeffs = np.polyfit(x, y, 1)
            dur_trend_slope = float(coeffs[0])  # positive = getting slower

        label = _classify_step(fail_rate, skip_rate, avg_dur, dur_trend_slope)

        patterns[step] = {
            "total_runs": total,
            "fail_rate": round(fail_rate, 3),
            "skip_rate": round(skip_rate, 3),
            "avg_duration_ms": int(avg_dur),
            "avg_retries": round(avg_retries, 2),
            "duration_trend_slope": round(dur_trend_slope, 1),
            "label": label,
            "confidence": _confidence(total, fail_rate, skip_rate),
        }

    return patterns


def _classify_step(fail_rate: float, skip_rate: float, avg_dur: float, slope: float) -> str:
    """Rule-based classifier trained on signal thresholds."""
    if skip_rate >= 0.8:
        return "redundant"
    if fail_rate >= 0.5:
        return "flaky"
    if slope > 5000 and avg_dur > 30_000:
        return "degrading"
    if avg_dur < 2000 and fail_rate < 0.05 and skip_rate < 0.1:
        return "trivial"
    if fail_rate >= 0.2:
        return "unstable"
    return "healthy"


def _confidence(total: int, fail_rate: float, skip_rate: float) -> float:
    """Higher sample count + consistent signal = higher confidence."""
    base = min(total / 20, 1.0) * 0.6  # up to 0.6 from volume
    signal_strength = abs(fail_rate - 0.5) + abs(skip_rate - 0.5)  # 0..1
    return round(min(base + signal_strength * 0.4, 1.0), 2)


def get_auto_remove_candidates(db: Session, repository_id: str | None = None, confidence_threshold: float = 0.6) -> list[dict]:
    """
    Return steps that should be auto-removed based on learned patterns.
    Only returns high-confidence candidates.
    """
    patterns = learn_step_patterns(db, repository_id)
    candidates = []
    for step, info in patterns.items():
        if info["label"] in {"redundant", "trivial"} and info["confidence"] >= confidence_threshold:
            candidates.append({
                "step": step,
                "label": info["label"],
                "confidence": info["confidence"],
                "reason": f"Learned from {info['total_runs']} runs: skip_rate={info['skip_rate']}, fail_rate={info['fail_rate']}",
            })
    return sorted(candidates, key=lambda x: x["confidence"], reverse=True)


def get_parallelize_candidates(db: Session, repository_id: str | None = None) -> list[dict]:
    """Return steps with degrading duration trend — candidates for parallelization."""
    patterns = learn_step_patterns(db, repository_id)
    candidates = []
    for step, info in patterns.items():
        if info["label"] == "degrading":
            candidates.append({
                "step": step,
                "avg_duration_ms": info["avg_duration_ms"],
                "trend_slope_ms_per_run": info["duration_trend_slope"],
                "confidence": info["confidence"],
                "reason": f"Duration increasing by ~{int(info['duration_trend_slope'])}ms/run over {info['total_runs']} runs",
            })
    return sorted(candidates, key=lambda x: x["trend_slope_ms_per_run"], reverse=True)


# ---------------------------------------------------------------------------
# Feedback-based tuning (secondary signal — enhances ML output)
# ---------------------------------------------------------------------------

def get_feedback_reinforced_candidates(db: Session, confidence_threshold: float = 0.6) -> dict[str, list[str]]:
    """
    Combine ML patterns with human feedback for higher-confidence decisions.
    Feedback reinforces (or overrides) ML labels.
    """
    ml_remove = {c["step"] for c in get_auto_remove_candidates(db, confidence_threshold=confidence_threshold)}

    feedback = db.query(RecommendationFeedback).all()
    feedback_votes: dict[str, list[str]] = defaultdict(list)
    for fb in feedback:
        job = (fb.comment or "").strip().lower()
        if job:
            feedback_votes[job].append(fb.vote)

    feedback_remove = set()
    feedback_keep = set()
    for job, votes in feedback_votes.items():
        total = len(votes)
        if total < 2:
            continue
        remove_ratio = votes.count("down") / total
        if remove_ratio >= 0.7:
            feedback_remove.add(job)
        elif remove_ratio <= 0.2:
            feedback_keep.add(job)

    # Final: ML says remove AND feedback doesn't say keep → high confidence
    confirmed_remove = list((ml_remove | feedback_remove) - feedback_keep)
    # ML says remove but feedback says keep → skip
    overridden = list(ml_remove & feedback_keep)

    return {
        "confirmed_remove": confirmed_remove,
        "feedback_only_remove": list(feedback_remove - ml_remove),
        "ml_only_remove": list(ml_remove - feedback_remove),
        "overridden_by_feedback": overridden,
    }


def explain_step(step: str, db: Session) -> str:
    """Human-readable explanation for a step's ML classification."""
    patterns = learn_step_patterns(db)
    info = patterns.get(step)
    if not info:
        return f"No historical data found for step '{step}'"

    label = info["label"]
    explanations = {
        "redundant": f"'{step}' is skipped {int(info['skip_rate']*100)}% of the time — safe to remove.",
        "flaky": f"'{step}' fails {int(info['fail_rate']*100)}% of the time — needs investigation or quarantine.",
        "degrading": f"'{step}' duration is increasing by ~{int(info['duration_trend_slope'])}ms/run — consider parallelizing.",
        "trivial": f"'{step}' completes in <2s and never fails — likely a no-op, safe to remove.",
        "unstable": f"'{step}' fails {int(info['fail_rate']*100)}% of the time — monitor closely.",
        "healthy": f"'{step}' is performing well across {info['total_runs']} runs.",
    }
    return explanations.get(label, f"'{step}' classified as '{label}'")
