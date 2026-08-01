"""
FRIS LLM Provider configuration API — API-key providers (OpenAI, Anthropic)
managed here directly; OAuth providers (Google, Azure) are configured via
api/oauth.py's start/callback flow, then also listed here.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import state
from api.deps import get_current_account
from core.llm_credential_service import API_KEY_PROVIDERS, OAUTH_PROVIDERS

router = APIRouter(prefix="/api/providers", tags=["providers"])


class SetApiKeyRequest(BaseModel):
    provider: str
    api_key: str


class SetDefaultRequest(BaseModel):
    provider: str


@router.get("")
async def list_providers(account: dict = Depends(get_current_account)):
    configured = await state.llm_credential_service.list_providers(str(account["id"]))
    default_provider = await state.llm_credential_service.get_default_provider(str(account["id"]))
    return {
        "configured": configured,
        "default_provider": default_provider,
        "available": {
            "apikey": sorted(API_KEY_PROVIDERS),
            "oauth": sorted(OAUTH_PROVIDERS),
        },
    }


@router.post("/apikey")
async def set_api_key(request: SetApiKeyRequest, account: dict = Depends(get_current_account)):
    if request.provider not in API_KEY_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"'{request.provider}' requires OAuth, not an API key")
    return await state.llm_credential_service.set_api_key(str(account["id"]), request.provider, request.api_key)


@router.post("/default")
async def set_default(request: SetDefaultRequest, account: dict = Depends(get_current_account)):
    await state.llm_credential_service.set_default(str(account["id"]), request.provider)
    return {"success": True}


@router.delete("/{provider}")
async def delete_provider(provider: str, account: dict = Depends(get_current_account)):
    await state.llm_credential_service.delete_credential(str(account["id"]), provider)
    return {"success": True}
