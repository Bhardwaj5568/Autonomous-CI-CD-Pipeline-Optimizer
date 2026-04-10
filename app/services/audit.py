from sqlalchemy.orm import Session

from app.models import AuditLog


def write_audit_log(db: Session, action_type: str, actor: str, details: dict) -> None:
    db.add(
        AuditLog(
            action_type=action_type,
            actor=actor,
            details=details,
        )
    )
