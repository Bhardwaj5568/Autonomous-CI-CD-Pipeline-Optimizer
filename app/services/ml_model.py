"""
Trained ML Model for pipeline step classification.

Uses scikit-learn RandomForestClassifier trained on PipelineEvent history.
Labels: healthy | flaky | redundant | degrading | trivial | unstable

Features per step:
  - fail_rate
  - skip_rate
  - avg_duration_ms (normalized)
  - duration_trend_slope (from linear regression)
  - avg_retries
  - run_count (log-scaled)
"""

from __future__ import annotations

import pickle
import os
from collections import defaultdict
from datetime import datetime
from statistics import mean
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PipelineEvent, MLModelSnapshot

# Where to persist the trained model
_MODEL_PATH = "optimizer_ml_model.pkl"

# Feature names (order matters — must match training)
FEATURES = [
    "fail_rate",
    "skip_rate",
    "avg_duration_ms_norm",
    "duration_trend_slope_norm",
    "avg_retries",
    "log_run_count",
]


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def _extract_features(db: Session, repository_id: str | None = None) -> tuple[np.ndarray, list[str], list[str]]:
    """
    Extract feature matrix from PipelineEvent history.
    Returns (X, labels, step_names).
    """
    query = select(PipelineEvent)
    if repository_id:
        query = query.where(PipelineEvent.repository_id == repository_id)
    query = query.order_by(PipelineEvent.created_at.desc()).limit(10000)

    rows = db.execute(query).scalars().all()
    if not rows:
        return np.array([]), [], []

    step_data: dict[str, list] = defaultdict(list)
    for r in rows:
        if r.stage_name:
            step_data[r.stage_name].append({
                "status": r.status,
                "duration_ms": r.duration_ms,
                "retry_count": r.retry_count,
            })

    X_rows = []
    step_names = []
    raw_labels = []

    for step, history in step_data.items():
        if len(history) < 3:
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

        # Duration trend via linear regression
        slope = 0.0
        if len(durations) >= 4:
            x = np.arange(len(durations), dtype=float)
            y = np.array(durations, dtype=float)
            slope = float(np.polyfit(x, y, 1)[0])

        # Derive label from rules (ground truth for training)
        label = _rule_label(fail_rate, skip_rate, avg_dur, slope)

        X_rows.append([
            fail_rate,
            skip_rate,
            avg_dur / 300_000,          # normalize to ~0-1 (5 min max)
            slope / 10_000,             # normalize slope
            avg_retries,
            np.log1p(total),            # log-scale run count
        ])
        step_names.append(step)
        raw_labels.append(label)

    if not X_rows:
        return np.array([]), [], []

    return np.array(X_rows, dtype=float), raw_labels, step_names


def _rule_label(fail_rate: float, skip_rate: float, avg_dur: float, slope: float) -> str:
    """Rule-based ground truth labels for training."""
    if skip_rate >= 0.8:
        return "redundant"
    if fail_rate >= 0.5:
        return "flaky"
    if 0.3 <= fail_rate < 0.5:
        return "unstable"
    if slope > 5000 and avg_dur > 30_000:
        return "degrading"
    if avg_dur < 2000 and fail_rate < 0.05:
        return "trivial"
    return "healthy"


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_model(db: Session) -> dict[str, Any]:
    """
    Train a RandomForestClassifier on PipelineEvent history.
    Saves model to disk and records snapshot in DB.
    Returns training report.
    """
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_score
    from sklearn.preprocessing import LabelEncoder

    X, labels, step_names = _extract_features(db)

    if len(X) < 10:
        return {
            "trained": False,
            "reason": f"Not enough data — need 10+ steps, found {len(X)}",
        }

    le = LabelEncoder()
    y = le.fit_transform(labels)

    clf = RandomForestClassifier(
        n_estimators=100,
        max_depth=6,
        min_samples_leaf=2,
        random_state=42,
        class_weight="balanced",
    )
    clf.fit(X, y)

    # Cross-validation accuracy
    cv_scores = cross_val_score(clf, X, y, cv=min(5, len(X) // 2), scoring="accuracy")
    accuracy = float(cv_scores.mean())

    # Feature importances
    feature_importances = dict(zip(FEATURES, [round(float(v), 4) for v in clf.feature_importances_]))

    # Label distribution
    from collections import Counter
    label_dist = dict(Counter(labels))

    # Save model + encoder to disk
    model_data = {"clf": clf, "le": le}
    with open(_MODEL_PATH, "wb") as f:
        pickle.dump(model_data, f)

    # Record snapshot in DB
    version = datetime.utcnow().strftime("v%Y%m%d_%H%M%S")
    try:
        db.add(MLModelSnapshot(
            model_version=version,
            algorithm="RandomForestClassifier",
            accuracy=round(accuracy, 4),
            training_samples=len(X),
            feature_importances=feature_importances,
            label_distribution=label_dist,
        ))
        db.commit()
    except Exception as e:
        print(f"[MLModel] DB snapshot error: {e}")

    return {
        "trained": True,
        "model_version": version,
        "training_samples": len(X),
        "accuracy": round(accuracy, 4),
        "cv_scores": [round(float(s), 4) for s in cv_scores],
        "feature_importances": feature_importances,
        "label_distribution": label_dist,
        "model_saved_to": _MODEL_PATH,
    }


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def _load_model() -> dict | None:
    """Load trained model from disk."""
    if not os.path.exists(_MODEL_PATH):
        return None
    try:
        with open(_MODEL_PATH, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def predict_step_labels(db: Session, repository_id: str | None = None) -> list[dict]:
    """
    Use trained ML model to predict labels for all steps.
    Falls back to rule-based if model not trained yet.
    """
    model_data = _load_model()

    X, rule_labels, step_names = _extract_features(db, repository_id)
    if len(X) == 0:
        return []

    if model_data is None:
        # Fallback: use rule-based labels
        return [
            {"step": name, "label": label, "source": "rule_based", "confidence": 0.7}
            for name, label in zip(step_names, rule_labels)
        ]

    clf = model_data["clf"]
    le = model_data["le"]

    # Predict with probabilities
    proba = clf.predict_proba(X)
    predicted_indices = np.argmax(proba, axis=1)
    predicted_labels = le.inverse_transform(predicted_indices)
    confidences = np.max(proba, axis=1)

    results = []
    for name, label, conf, rule_label in zip(step_names, predicted_labels, confidences, rule_labels):
        results.append({
            "step": name,
            "label": label,
            "confidence": round(float(conf), 3),
            "rule_label": rule_label,
            "agreement": label == rule_label,
            "source": "ml_model",
        })

    return sorted(results, key=lambda x: x["confidence"], reverse=True)


def get_model_status(db: Session) -> dict[str, Any]:
    """Return current model status and latest snapshot."""
    model_exists = os.path.exists(_MODEL_PATH)

    latest = db.execute(
        select(MLModelSnapshot).order_by(MLModelSnapshot.created_at.desc()).limit(1)
    ).scalar_one_or_none()

    return {
        "model_trained": model_exists,
        "model_path": _MODEL_PATH if model_exists else None,
        "latest_snapshot": {
            "version": latest.model_version,
            "accuracy": latest.accuracy,
            "training_samples": latest.training_samples,
            "algorithm": latest.algorithm,
            "feature_importances": latest.feature_importances,
            "label_distribution": latest.label_distribution,
            "trained_at": latest.created_at.isoformat(),
        } if latest else None,
    }
