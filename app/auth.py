from collections.abc import Callable

from fastapi import Header, HTTPException

from app.config import settings


def require_role(allowed_roles: set[str]) -> Callable:
    def dependency(x_api_key: str | None = Header(default=None), x_role: str | None = Header(default=None)):
        if settings.app_api_key:
            if x_api_key != settings.app_api_key:
                raise HTTPException(status_code=401, detail="Invalid API key")

        if x_role is None:
            raise HTTPException(status_code=403, detail="Role header is required")

        if x_role.lower() not in {r.lower() for r in allowed_roles}:
            raise HTTPException(status_code=403, detail="Insufficient role")

        return {"role": x_role.lower()}

    return dependency
