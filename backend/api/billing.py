"""
FRIS Billing API — postpaid metered usage ledger, summaries, and simple
monthly invoice rollups. No payment processor wired in; this computes and
displays from the usage_events ledger (core/metering_service.py).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

import state
from api.deps import get_current_account, require_role

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.get("/usage")
async def get_usage(
    event_type: Optional[str] = None,
    since_days: Optional[int] = None,
    limit: int = 200,
    offset: int = 0,
    account: dict = Depends(get_current_account),
):
    since = datetime.now(timezone.utc) - timedelta(days=since_days) if since_days else None
    return await state.metering_service.get_usage(
        str(account["id"]), event_type=event_type, since=since, limit=limit, offset=offset
    )


@router.get("/summary")
async def get_summary(since_days: int = 30, account: dict = Depends(get_current_account)):
    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    return await state.metering_service.get_summary(str(account["id"]), since=since)


@router.get("/invoices")
async def get_invoices(months: int = 6, account: dict = Depends(get_current_account)):
    return {"invoices": await state.metering_service.get_invoices(str(account["id"]), months=months)}


@router.get("/pricing")
async def get_pricing(account: dict = Depends(get_current_account)):
    return {"pricing": await state.metering_service.get_pricing_table()}


@router.put("/pricing/{event_type}")
async def update_pricing(
    event_type: str, unit_cost_usd: float, tokens_per_unit: float,
    account: dict = Depends(require_role("owner", "admin"))
):
    # Note: the pricing table is global (metering_service owns one table for
    # every tenant), not per-organization, so this is gated to owner/admin
    # role as a stopgap rather than a true platform-superadmin check — a
    # full platform-vs-tenant role split is future work, not this batch.
    await state.metering_service.update_pricing(event_type, unit_cost_usd, tokens_per_unit)
    return {"success": True}
