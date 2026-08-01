"""
OAuth connect flow — only for providers that require it (Google Gemini via
Google Cloud, Azure OpenAI). Everything else uses a plain API key
(see api/providers.py). Standard authorization-code exchange; the
resulting refresh token is encrypted and stored via LLMCredentialService.

Note: the `state` query param here is Google/Azure's OAuth CSRF token,
unrelated to this backend's state.py singleton-holder module.
"""

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

import state
from config import settings

router = APIRouter(prefix="/api/providers", tags=["oauth"])

# In-memory CSRF-state -> account_id map. A connect flow is short-lived
# (seconds), so this doesn't need to survive a restart.
_pending_oauth: dict[str, str] = {}

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
AZURE_AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
AZURE_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"

PROVIDER_CONFIG = {
    "google": {
        "auth_url": GOOGLE_AUTH_URL,
        "token_url": GOOGLE_TOKEN_URL,
        "client_id": lambda: settings.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": lambda: settings.GOOGLE_OAUTH_CLIENT_SECRET,
        "scope": "https://www.googleapis.com/auth/generative-language.retriever",
    },
    "azure": {
        "auth_url": AZURE_AUTH_URL,
        "token_url": AZURE_TOKEN_URL,
        "client_id": lambda: settings.AZURE_OAUTH_CLIENT_ID,
        "client_secret": lambda: settings.AZURE_OAUTH_CLIENT_SECRET,
        "scope": "https://cognitiveservices.azure.com/.default offline_access",
    },
}


@router.get("/{provider}/oauth/start")
async def oauth_start(provider: str, account_id: str = Query(...)):
    cfg = PROVIDER_CONFIG.get(provider)
    if not cfg:
        raise HTTPException(status_code=400, detail=f"'{provider}' does not use OAuth")
    if not cfg["client_id"]():
        raise HTTPException(status_code=503, detail=f"{provider} OAuth is not configured on this server")

    csrf_token = secrets.token_urlsafe(24)
    _pending_oauth[csrf_token] = account_id

    redirect_uri = f"{settings.OAUTH_REDIRECT_BASE}/api/providers/{provider}/oauth/callback"
    params = {
        "client_id": cfg["client_id"](),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": cfg["scope"],
        "access_type": "offline",
        "prompt": "consent",
        "state": csrf_token,
    }
    return RedirectResponse(f"{cfg['auth_url']}?{urlencode(params)}")


@router.get("/{provider}/oauth/callback")
async def oauth_callback(provider: str, code: str, state_param: str = Query(..., alias="state")):
    cfg = PROVIDER_CONFIG.get(provider)
    if not cfg:
        raise HTTPException(status_code=400, detail=f"'{provider}' does not use OAuth")

    account_id = _pending_oauth.pop(state_param, None)
    if not account_id:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    redirect_uri = f"{settings.OAUTH_REDIRECT_BASE}/api/providers/{provider}/oauth/callback"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(cfg["token_url"], data={
            "code": code,
            "client_id": cfg["client_id"](),
            "client_secret": cfg["client_secret"](),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        resp.raise_for_status()
        token_data = resp.json()

    refresh_token = token_data.get("refresh_token") or token_data.get("access_token")
    expires_in = token_data.get("expires_in", 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    await state.llm_credential_service.set_oauth_tokens(account_id, provider, refresh_token, expires_at)

    return {"success": True, "provider": provider, "message": f"{provider} connected successfully"}
