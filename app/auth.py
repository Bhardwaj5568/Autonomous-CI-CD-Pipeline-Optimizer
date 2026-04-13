"""
Role-Based Access Control (RBAC) Module

This module provides authentication and role validation for FastAPI endpoints.
It enforces both API key authentication (optional) and role-based authorization.

Roles supported:
  - viewer: Read-only access to runs, assessments, KPIs
  - operator: Full operational access including webhook intake and scoring
  - admin: Administrative access including audit logs

Usage:
    from fastapi import Depends
    
    @app.get("/data")
    def get_data(_: dict = Depends(require_role({"viewer", "admin"}))):
        return data
"""

from collections.abc import Callable

from fastapi import Header, HTTPException

from app.config import settings


def require_role(allowed_roles: set[str]) -> Callable:
    """
    FastAPI dependency for role-based access control.
    
    This function creates a dependency that validates:
    1. API key (if APP_API_KEY environment variable is set)
    2. Role header presence and validity
    
    Args:
        allowed_roles: Set of allowed role names (case-insensitive)
    
    Returns:
        Dependency function that validates headers and returns user context
    
    Raises:
        HTTPException(401): API key is missing or invalid
        HTTPException(403): Role header missing or insufficient permissions
    
    Examples:
        require_role({"viewer", "operator"})
        require_role({"admin"})
    """
    def dependency(
        x_api_key: str | None = Header(default=None),
        x_role: str | None = Header(default=None)
    ):
        # API Key Validation: Only enforce if APP_API_KEY is configured in environment
        if settings.app_api_key:
            if x_api_key != settings.app_api_key:
                raise HTTPException(status_code=401, detail="Invalid API key")

        # Role Validation: Role header is always required
        if x_role is None:
            raise HTTPException(status_code=403, detail="Role header is required")

        # Role Check: Ensure user's role is in the allowed set (case-insensitive comparison)
        if x_role.lower() not in {r.lower() for r in allowed_roles}:
            raise HTTPException(status_code=403, detail="Insufficient role")

        # Return validated user context for use in endpoint handlers
        return {"role": x_role.lower()}

    return dependency
