"""
AutoOptimizer — Zero-touch action engine.
Wires PipelineOptimizerEngine to CI/CD clients and audit logger.
Called automatically from scoring.py after every run is scored.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session


class ActionResult:
    def __init__(self, action: str, target: str, result: str, details: dict[str, Any] | None = None):
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.action = action
        self.target = target
        self.result = result
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "target": self.target,
            "result": self.result,
            "details": self.details,
        }


class AuditLogger:
    def __init__(self, db: Session | None = None):
        self.db = db

    def log(self, entry: dict[str, Any]) -> None:
        from app.models import AuditLog
        if self.db:
            try:
                self.db.add(AuditLog(
                    action_type=entry.get("action", "auto_optimizer"),
                    actor="auto-optimizer",
                    details=entry,
                ))
                self.db.commit()
            except Exception as e:
                print(f"[AuditLogger] DB write failed: {e}")
        print(f"[AUDIT] {entry}")


class AutoOptimizer:
    """
    Central zero-touch engine.
    On every scored run:
      - If recommendation is 'block' → cancel the run via CI/CD client
      - If recommendation is 'deploy' → run full optimization cycle
      - Always logs to audit trail
    """

    def __init__(self, cicd_clients: dict[str, Any], audit_logger: AuditLogger, db: Session | None = None):
        self.cicd_clients = cicd_clients
        self.audit_logger = audit_logger
        self.db = db

    def handle_recommendation(self, recommendation: dict[str, Any]) -> ActionResult:
        action = recommendation.get("action", "deploy")
        system = recommendation.get("system", "unknown")
        run_id = recommendation.get("run_id", "unknown")
        client = self.cicd_clients.get(system)

        result = "skipped"
        details: dict[str, Any] = {"system": system, "run_id": run_id}

        try:
            if action == "block":
                result, details = self._handle_block(client, system, run_id, recommendation)

            elif action in {"deploy", "canary"}:
                result, details = self._handle_optimize(system, run_id, recommendation)

            elif action == "delay":
                result = "noted"
                details["message"] = "Delay recommendation recorded. No automated action taken."

            else:
                result = "unknown_action"
                details["message"] = f"Unrecognized action: {action}"

        except Exception as e:
            result = "error"
            details["error"] = str(e)

        action_result = ActionResult(action, run_id, result, details)
        self.audit_logger.log(action_result.to_dict())
        return action_result

    def _handle_block(self, client, system: str, run_id: str, rec: dict) -> tuple[str, dict]:
        if not client:
            return "no_client", {"message": f"No client configured for {system}. Set {system.upper()}_TOKEN in .env"}

        if system == "github_actions":
            owner = getattr(client, "owner", None) or rec.get("repo_owner")
            repo = getattr(client, "repo", None) or rec.get("repo_name")
            if owner and repo:
                resp = client.block_deployment(run_id)
                return "success", {"action": "cancelled_run", "response": resp}

        elif system == "gitlab_ci":
            project_id = getattr(client, "project_id", None) or rec.get("gitlab_project_id")
            if project_id:
                resp = client.block_pipeline(project_id, run_id)
                return "success", {"action": "cancelled_pipeline", "response": resp}

        elif system == "jenkins":
            job_name = rec.get("jenkins_job_name", run_id)
            build_number = rec.get("build_number", run_id)
            resp = client.block_build(job_name, build_number)
            return "success", {"action": "stopped_build", "response": resp}

        return "skipped", {"message": "Missing required identifiers for block action"}

    def _handle_optimize(self, system: str, run_id: str, rec: dict) -> tuple[str, dict]:
        """Run the full optimization cycle for a scored run."""
        if not self.db:
            return "skipped", {"message": "No DB session — cannot run optimization"}

        from app.services.pipeline_optimizer import PipelineOptimizerEngine
        from app.models import PipelineEvent

        # Fetch events for this run
        from sqlalchemy import select
        rows = self.db.execute(
            select(PipelineEvent).where(PipelineEvent.run_id == run_id)
        ).scalars().all()

        if not rows:
            return "skipped", {"message": "No events found for run"}

        events = [
            {
                "stage_name": r.stage_name,
                "status": r.status,
                "duration_ms": r.duration_ms,
                "retry_count": r.retry_count,
            }
            for r in rows
        ]

        repository_id = rows[0].repository_id
        pipeline_id = rows[0].pipeline_id

        github_client = self.cicd_clients.get("github_actions")
        gitlab_client = self.cicd_clients.get("gitlab_ci")
        jenkins_client = self.cicd_clients.get("jenkins")

        engine = PipelineOptimizerEngine(
            db=self.db,
            github_client=github_client,
            gitlab_client=gitlab_client,
            jenkins_client=jenkins_client,
        )

        # dry_run=True unless all required params are present
        can_apply = (
            (system == "github_actions" and github_client and rec.get("repo_owner") and rec.get("repo_name") and rec.get("workflow_path"))
            or (system == "gitlab_ci" and gitlab_client and rec.get("gitlab_project_id"))
            or (system == "jenkins" and jenkins_client and rec.get("jenkins_job_name"))
        )

        opt_result = engine.run(
            events=events,
            source_system=system,
            repository_id=repository_id,
            pipeline_id=pipeline_id,
            repo_owner=rec.get("repo_owner"),
            repo_name=rec.get("repo_name"),
            workflow_path=rec.get("workflow_path"),
            gitlab_project_id=rec.get("gitlab_project_id"),
            jenkins_job_name=rec.get("jenkins_job_name"),
            dry_run=not can_apply,
        )

        return "success", opt_result
