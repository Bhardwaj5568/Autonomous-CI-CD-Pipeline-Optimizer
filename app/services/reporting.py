"""
Reporting service — Build time trend, before/after comparison, 42% reduction proof.
"""

from __future__ import annotations

import io
from collections import defaultdict
from statistics import mean
from typing import Any

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — no display needed
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PipelineRun, RiskAssessment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_runs(db: Session, repo_id: str, pipeline_id: str) -> list[PipelineRun]:
    return db.execute(
        select(PipelineRun)
        .where(PipelineRun.repository_id == repo_id)
        .where(PipelineRun.pipeline_id == pipeline_id)
        .order_by(PipelineRun.created_at.asc())
    ).scalars().all()


def _split_baseline_optimized(runs: list[PipelineRun]) -> tuple[list, list]:
    """
    Split runs into baseline (first 40%) and optimized (last 40%).
    Middle 20% is transition period — excluded for clean comparison.
    """
    n = len(runs)
    if n < 5:
        return runs, []
    baseline_end = int(n * 0.4)
    optimized_start = int(n * 0.6)
    return runs[:baseline_end], runs[optimized_start:]


def _compute_reduction(baseline_runs: list, optimized_runs: list) -> dict[str, Any]:
    """Compute actual build time reduction % with statistical confidence."""
    if not baseline_runs or not optimized_runs:
        return {}

    baseline_durations = [r.total_duration_ms for r in baseline_runs if r.total_duration_ms > 0]
    optimized_durations = [r.total_duration_ms for r in optimized_runs if r.total_duration_ms > 0]

    if not baseline_durations or not optimized_durations:
        return {}

    baseline_avg = mean(baseline_durations)
    optimized_avg = mean(optimized_durations)
    reduction_pct = ((baseline_avg - optimized_avg) / baseline_avg) * 100 if baseline_avg > 0 else 0

    # Linear regression slope on optimized period — confirms downward trend
    y = np.array(optimized_durations, dtype=float)
    x = np.arange(len(y))
    slope = float(np.polyfit(x, y, 1)[0]) if len(y) >= 2 else 0.0

    return {
        "baseline_avg_ms": int(baseline_avg),
        "optimized_avg_ms": int(optimized_avg),
        "reduction_percent": round(reduction_pct, 1),
        "baseline_runs": len(baseline_durations),
        "optimized_runs": len(optimized_durations),
        "trend_slope_ms_per_run": round(slope, 1),
        "trend_direction": "improving" if slope < 0 else "degrading" if slope > 0 else "stable",
        "meets_42_percent_target": reduction_pct >= 42.0,
    }


# ---------------------------------------------------------------------------
# 1. Basic trend chart (existing endpoint — improved)
# ---------------------------------------------------------------------------

def plot_build_time_trend(
    db: Session,
    repo_id: str,
    pipeline_id: str,
    save_path: str = "build_time_trend.png",
) -> str | None:
    runs = _get_runs(db, repo_id, pipeline_id)
    if not runs:
        return None

    durations = [r.total_duration_ms / 1000 for r in runs]
    labels = [r.created_at.strftime("%m/%d %H:%M") for r in runs]
    x = np.arange(len(durations))

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(x, durations, marker="o", color="#1f77b4", linewidth=2, markersize=5, label="Build Duration")

    # Regression line
    if len(durations) >= 3:
        coeffs = np.polyfit(x, durations, 1)
        trend = np.polyval(coeffs, x)
        color = "#2ca02c" if coeffs[0] < 0 else "#d62728"
        ax.plot(x, trend, "--", color=color, linewidth=1.5, label=f"Trend ({'improving' if coeffs[0] < 0 else 'degrading'})")

    ax.set_title(f"Build Time Trend — {repo_id} / {pipeline_id}", fontsize=14, fontweight="bold")
    ax.set_xlabel("Pipeline Run")
    ax.set_ylabel("Duration (seconds)")
    ax.set_xticks(x[::max(1, len(x) // 10)])
    ax.set_xticklabels(labels[::max(1, len(x) // 10)], rotation=30, ha="right", fontsize=8)
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)
    return save_path


# ---------------------------------------------------------------------------
# 2. Before / After comparison chart — the 42% proof
# ---------------------------------------------------------------------------

def plot_build_time_reduction(
    db: Session,
    repo_id: str,
    pipeline_id: str,
    save_path: str = "build_time_reduction.png",
) -> tuple[str | None, dict]:
    """
    Generate a before/after bar + line chart proving build time reduction.
    Returns (save_path, reduction_stats_dict).
    """
    runs = _get_runs(db, repo_id, pipeline_id)
    if len(runs) < 5:
        return None, {"error": f"Need at least 5 runs, found {len(runs)}"}

    baseline_runs, optimized_runs = _split_baseline_optimized(runs)
    stats = _compute_reduction(baseline_runs, optimized_runs)
    if not stats:
        return None, {"error": "Insufficient duration data"}

    baseline_s = stats["baseline_avg_ms"] / 1000
    optimized_s = stats["optimized_avg_ms"] / 1000
    reduction = stats["reduction_percent"]

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(
        f"Build Time Reduction Analysis — {repo_id}",
        fontsize=15, fontweight="bold", y=1.01,
    )

    # --- Left: Bar chart (before vs after) ---
    ax1 = axes[0]
    bars = ax1.bar(
        ["Before\nOptimization", "After\nOptimization"],
        [baseline_s, optimized_s],
        color=["#d62728", "#2ca02c"],
        width=0.5,
        edgecolor="white",
        linewidth=1.5,
    )
    for bar, val in zip(bars, [baseline_s, optimized_s]):
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.5,
            f"{val:.0f}s",
            ha="center", va="bottom", fontsize=13, fontweight="bold",
        )

    # Reduction annotation arrow
    ax1.annotate(
        f"↓ {reduction:.1f}% reduction",
        xy=(1, optimized_s),
        xytext=(0.5, (baseline_s + optimized_s) / 2),
        fontsize=12, fontweight="bold", color="#2ca02c",
        arrowprops=dict(arrowstyle="->", color="#2ca02c", lw=1.5),
        ha="center",
    )

    target_line = baseline_s * 0.58  # 42% reduction target
    ax1.axhline(target_line, color="#ff7f0e", linestyle="--", linewidth=1.5, label="42% target")
    ax1.legend(fontsize=10)
    ax1.set_ylabel("Avg Build Duration (seconds)", fontsize=11)
    ax1.set_title("Average Build Time: Before vs After", fontsize=12)
    ax1.set_ylim(0, baseline_s * 1.3)
    ax1.grid(axis="y", alpha=0.3)

    # --- Right: Full timeline with baseline/optimized shading ---
    ax2 = axes[1]
    all_durations = [r.total_duration_ms / 1000 for r in runs]
    all_x = np.arange(len(all_durations))
    n = len(runs)
    baseline_end = int(n * 0.4)
    optimized_start = int(n * 0.6)

    ax2.plot(all_x, all_durations, color="#1f77b4", linewidth=1.5, marker="o", markersize=3, label="Build Duration")

    # Shade regions
    ax2.axvspan(0, baseline_end, alpha=0.12, color="#d62728", label="Baseline period")
    ax2.axvspan(optimized_start, n, alpha=0.12, color="#2ca02c", label="Optimized period")

    # Baseline avg line
    ax2.axhline(baseline_s, color="#d62728", linestyle="--", linewidth=1.2, alpha=0.7, label=f"Baseline avg ({baseline_s:.0f}s)")
    ax2.axhline(optimized_s, color="#2ca02c", linestyle="--", linewidth=1.2, alpha=0.7, label=f"Optimized avg ({optimized_s:.0f}s)")

    # Regression on full timeline
    if len(all_durations) >= 3:
        coeffs = np.polyfit(all_x, all_durations, 1)
        trend = np.polyval(coeffs, all_x)
        ax2.plot(all_x, trend, "-", color="#9467bd", linewidth=1.5, alpha=0.6, label="Overall trend")

    ax2.set_title("Full Timeline with Optimization Phases", fontsize=12)
    ax2.set_xlabel("Pipeline Run #")
    ax2.set_ylabel("Duration (seconds)")
    ax2.legend(fontsize=8, loc="upper right")
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return save_path, stats


# ---------------------------------------------------------------------------
# 3. Multi-pipeline comparison chart
# ---------------------------------------------------------------------------

def plot_multi_pipeline_comparison(
    db: Session,
    save_path: str = "multi_pipeline_comparison.png",
) -> tuple[str | None, list[dict]]:
    """
    Compare build time reduction across ALL pipelines in the DB.
    Shows which pipelines improved the most.
    """
    # Get all unique repo+pipeline combos
    from sqlalchemy import distinct
    combos = db.execute(
        select(PipelineRun.repository_id, PipelineRun.pipeline_id).distinct()
    ).all()

    if not combos:
        return None, []

    results = []
    for repo_id, pipeline_id in combos:
        runs = _get_runs(db, repo_id, pipeline_id)
        if len(runs) < 5:
            continue
        baseline, optimized = _split_baseline_optimized(runs)
        stats = _compute_reduction(baseline, optimized)
        if stats:
            stats["repository_id"] = repo_id
            stats["pipeline_id"] = pipeline_id
            results.append(stats)

    if not results:
        return None, []

    # Sort by reduction %
    results.sort(key=lambda x: x["reduction_percent"], reverse=True)

    labels = [f"{r['repository_id'][:12]}\n{r['pipeline_id'][:12]}" for r in results]
    reductions = [r["reduction_percent"] for r in results]
    colors = ["#2ca02c" if r >= 42 else "#ff7f0e" if r >= 20 else "#d62728" for r in reductions]

    fig, ax = plt.subplots(figsize=(max(8, len(results) * 2), 6))
    bars = ax.bar(labels, reductions, color=colors, edgecolor="white", linewidth=1.2)

    for bar, val in zip(bars, reductions):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.5,
            f"{val:.1f}%",
            ha="center", va="bottom", fontsize=10, fontweight="bold",
        )

    ax.axhline(42, color="#ff7f0e", linestyle="--", linewidth=2, label="42% target")
    ax.set_title("Build Time Reduction by Pipeline", fontsize=14, fontweight="bold")
    ax.set_ylabel("Reduction (%)")
    ax.set_ylim(0, max(max(reductions) * 1.2, 50))
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    green_patch = mpatches.Patch(color="#2ca02c", label="≥42% (target met)")
    orange_patch = mpatches.Patch(color="#ff7f0e", label="20-42% (partial)")
    red_patch = mpatches.Patch(color="#d62728", label="<20% (needs work)")
    ax.legend(handles=[green_patch, orange_patch, red_patch], loc="upper right")

    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return save_path, results


# ---------------------------------------------------------------------------
# 4. Optimization summary — JSON proof (no chart needed)
# ---------------------------------------------------------------------------

def compute_optimization_summary(db: Session) -> dict[str, Any]:
    """
    Compute full optimization proof across all pipelines.
    Returns structured data proving build time reduction.
    """
    from sqlalchemy import distinct

    combos = db.execute(
        select(PipelineRun.repository_id, PipelineRun.pipeline_id).distinct()
    ).all()

    pipeline_results = []
    total_baseline_ms = 0
    total_optimized_ms = 0

    for repo_id, pipeline_id in combos:
        runs = _get_runs(db, repo_id, pipeline_id)
        if len(runs) < 5:
            continue
        baseline, optimized = _split_baseline_optimized(runs)
        stats = _compute_reduction(baseline, optimized)
        if not stats:
            continue
        stats["repository_id"] = repo_id
        stats["pipeline_id"] = pipeline_id
        pipeline_results.append(stats)
        total_baseline_ms += stats["baseline_avg_ms"] * stats["baseline_runs"]
        total_optimized_ms += stats["optimized_avg_ms"] * stats["optimized_runs"]

    if not pipeline_results:
        return {
            "status": "insufficient_data",
            "message": "Need at least 5 runs per pipeline to compute reduction proof",
            "pipelines_analyzed": 0,
        }

    # Overall weighted reduction
    total_baseline_runs = sum(r["baseline_runs"] for r in pipeline_results)
    total_optimized_runs = sum(r["optimized_runs"] for r in pipeline_results)
    overall_baseline_avg = total_baseline_ms / max(total_baseline_runs, 1)
    overall_optimized_avg = total_optimized_ms / max(total_optimized_runs, 1)
    overall_reduction = ((overall_baseline_avg - overall_optimized_avg) / overall_baseline_avg * 100) if overall_baseline_avg > 0 else 0

    pipelines_meeting_target = [r for r in pipeline_results if r["meets_42_percent_target"]]
    avg_reduction = mean([r["reduction_percent"] for r in pipeline_results])

    return {
        "status": "computed",
        "overall_reduction_percent": round(overall_reduction, 1),
        "average_reduction_percent": round(avg_reduction, 1),
        "meets_42_percent_target": overall_reduction >= 42.0,
        "target": "42% build time reduction",
        "pipelines_analyzed": len(pipeline_results),
        "pipelines_meeting_target": len(pipelines_meeting_target),
        "overall_baseline_avg_seconds": round(overall_baseline_avg / 1000, 1),
        "overall_optimized_avg_seconds": round(overall_optimized_avg / 1000, 1),
        "time_saved_per_run_seconds": round((overall_baseline_avg - overall_optimized_avg) / 1000, 1),
        "pipeline_breakdown": pipeline_results,
        "methodology": (
            "Baseline = avg of first 40% of runs. "
            "Optimized = avg of last 40% of runs. "
            "Middle 20% excluded as transition period. "
            "Reduction = (baseline_avg - optimized_avg) / baseline_avg * 100."
        ),
    }
