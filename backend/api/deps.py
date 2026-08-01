"""
Shared FastAPI dependencies for auth. Accepts either a console session
(Bearer JWT) or a programmatic API key (X-API-Key header).

Every account returned here carries organization_id (the tenant boundary —
every connector, log, and fused graph entity is scoped to it) and role
(owner/admin/member/viewer, from core.account_service.ROLES). Routes that
read or write tenant data must pass account["organization_id"] into the
service layer; routes that perform privileged actions should additionally
depend on require_role(...).
"""

from typing import Optional

from fastapi import Depends, Header, HTTPException

import state
from core.account_service import ROLES


async def get_current_account(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> dict:
    account_service = state.account_service
    if x_api_key:
        account = await account_service.authenticate_api_key(x_api_key)
        if account:
            return account
        raise HTTPException(status_code=401, detail="Invalid API key")

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        account_id = account_service.decode_jwt(token)
        if account_id:
            account = await account_service.get_account(account_id)
            if account:
                return account
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    raise HTTPException(status_code=401, detail="Authentication required")


def require_role(*allowed_roles: str):
    """
    Dependency factory: gates a route to accounts whose role is one of
    allowed_roles. Stacks on top of get_current_account rather than
    replacing it, so every route keeps the same auth extraction logic.

    Usage: @router.delete(...) async def x(account: dict = Depends(require_role("owner", "admin"))): ...
    """
    invalid = set(allowed_roles) - set(ROLES)
    if invalid:
        raise ValueError(f"require_role got unknown role(s): {invalid}. Valid roles: {ROLES}")

    async def dependency(account: dict = Depends(get_current_account)) -> dict:
        if account.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"This action requires one of roles {list(allowed_roles)}, "
                       f"account has role '{account.get('role')}'"
            )
        return account

    return dependency
