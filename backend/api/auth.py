"""
FRIS Auth API — signup/login (JWT sessions for the console) and API key
management (for programmatic access).
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

import state
from api.deps import get_current_account, require_role
from core.account_service import ROLES

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class CreateApiKeyRequest(BaseModel):
    name: str


class InviteRequest(BaseModel):
    email: EmailStr
    role: str = "member"


class AcceptInviteRequest(BaseModel):
    token: str
    password: str = Field(..., min_length=8)
    name: str | None = None


class UpdateRoleRequest(BaseModel):
    role: str


@router.post("/signup")
async def signup(request: SignupRequest):
    try:
        account = await state.account_service.create_account(request.email, request.password, request.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    token = state.account_service.create_jwt(account["id"])
    return {"token": token, "account": account}


@router.post("/login")
async def login(request: LoginRequest):
    account = await state.account_service.authenticate(request.email, request.password)
    if not account:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = state.account_service.create_jwt(account["id"])
    return {"token": token, "account": account}


@router.get("/me")
async def me(account: dict = Depends(get_current_account)):
    return {"account": {
        "id": str(account["id"]), "email": account["email"], "name": account["name"],
        "organization_id": account.get("organization_id"), "role": account.get("role")
    }}


@router.post("/keys")
async def create_key(request: CreateApiKeyRequest, account: dict = Depends(get_current_account)):
    return await state.account_service.create_api_key(str(account["id"]), request.name)


@router.get("/keys")
async def list_keys(account: dict = Depends(get_current_account)):
    return {"keys": await state.account_service.list_api_keys(str(account["id"]))}


@router.delete("/keys/{key_id}")
async def revoke_key(key_id: str, account: dict = Depends(get_current_account)):
    await state.account_service.revoke_api_key(str(account["id"]), key_id)
    return {"success": True}


# ============================================================
# Team management — invite teammates into the same organization,
# view/manage the roster, change roles. Owner/admin only for anything
# that changes who has access; any member can view the roster.
# ============================================================

@router.post("/invite")
async def invite_member(request: InviteRequest, account: dict = Depends(require_role("owner", "admin"))):
    if request.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {ROLES}")
    try:
        invite = await state.account_service.create_invite(
            organization_id=account["organization_id"], email=request.email,
            role=request.role, invited_by=str(account["id"])
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if state.logging_service:
        from core.logging_service import LogCategory, LogLevel
        await state.logging_service.log(
            category=LogCategory.APP, level=LogLevel.INFO,
            message=f"{account['email']} invited {request.email} as {request.role}",
            source="auth.invite_member", organization_id=account["organization_id"]
        )
    # No SMTP wired into this deployment — the invite link is returned
    # directly for the inviter to copy and share, same manual convention
    # the rest of this codebase uses (e.g. billing has no payment processor).
    return {"invite": invite, "accept_url_path": f"/accept-invite?token={invite['token']}"}


@router.get("/invites")
async def list_invites(account: dict = Depends(require_role("owner", "admin"))):
    return {"invites": await state.account_service.list_invites(account["organization_id"])}


@router.delete("/invites/{invite_id}")
async def revoke_invite(invite_id: str, account: dict = Depends(require_role("owner", "admin"))):
    revoked = await state.account_service.revoke_invite(account["organization_id"], invite_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Invite not found or already accepted")
    return {"success": True}


@router.post("/accept-invite")
async def accept_invite(request: AcceptInviteRequest):
    """No auth required — the person accepting doesn't have an account yet. The invite token itself is the credential."""
    try:
        account = await state.account_service.accept_invite(request.token, request.password, request.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    token = state.account_service.create_jwt(account["id"])
    return {"token": token, "account": account}


@router.get("/team")
async def list_team(account: dict = Depends(get_current_account)):
    return {"members": await state.account_service.list_team(account["organization_id"])}


@router.put("/team/{account_id}/role")
async def update_member_role(account_id: str, request: UpdateRoleRequest, account: dict = Depends(require_role("owner"))):
    if request.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {ROLES}")
    try:
        result = await state.account_service.update_member_role(account["organization_id"], account_id, request.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "member": result}


@router.delete("/team/{account_id}")
async def remove_member(account_id: str, account: dict = Depends(require_role("owner"))):
    if account_id == str(account["id"]):
        raise HTTPException(status_code=400, detail="Cannot remove your own account from the team this way")
    try:
        removed = await state.account_service.remove_member(account["organization_id"], account_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not removed:
        raise HTTPException(status_code=404, detail="No such account in this organization")
    return {"success": True}
