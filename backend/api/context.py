"""
FRIS Context API — CRUD over the living context layer (core/context_service.py).
Read is open to any authenticated account in the tenant; writes require
admin/owner since a canonical-field definition affects every query and
agent answer for the whole organization, not just the account that set it.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import state
from api.deps import get_current_account, require_role

router = APIRouter(prefix="/api/context", tags=["context"])


class UpsertContextRequest(BaseModel):
    canonical_field: str
    description: Optional[str] = None


@router.get("")
async def list_context(entity_type: Optional[str] = None, account: dict = Depends(get_current_account)):
    definitions = await state.context_service.list_definitions(
        organization_id=account["organization_id"], entity_type=entity_type
    )
    return {"definitions": definitions}


@router.put("/{entity_type}/{property_name}")
async def upsert_context(
    entity_type: str, property_name: str, request: UpsertContextRequest,
    account: dict = Depends(require_role("owner", "admin"))
):
    definition = await state.context_service.upsert_definition(
        organization_id=account["organization_id"], entity_type=entity_type, property_name=property_name,
        canonical_field=request.canonical_field, description=request.description
    )
    return {"success": True, "definition": definition}


@router.delete("/{entity_type}/{property_name}")
async def delete_context(entity_type: str, property_name: str, account: dict = Depends(require_role("owner", "admin"))):
    deleted = await state.context_service.delete_definition(
        organization_id=account["organization_id"], entity_type=entity_type, property_name=property_name
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="No such context definition")
    return {"success": True}
