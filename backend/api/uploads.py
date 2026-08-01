"""
FRIS Uploads API — browser-based file upload for CSV/Excel/document
connectors, replacing the server-file-path-only model, plus the Data
Viewer: lets a user see exactly what FRIS saw in their file (raw rows for
CSV/Excel, extracted text and entities for PDF/email) before and after
confirming a connector, rather than trusting a black box.

Every upload is stored via core/storage_service.py, tenant-scoped and
encrypted at rest — the same object_id is both what a connector config
references and what the Data Viewer re-fetches to render.
"""

import base64
import csv
import io
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

import state
from api.deps import get_current_account

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

# Uploads feeding connector configs go straight into a JSONB column as
# base64 — same ceiling the existing CSV/Excel file_content convention
# already implies. 25MB keeps that column, and the encrypted-at-rest copy
# in storage_service, well within Postgres's comfort zone.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@router.post("")
async def upload_file(file: UploadFile = File(...), account: dict = Depends(get_current_account)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024*1024)}MB upload limit")

    object_id = state.storage_service.put(account["organization_id"], file.filename or "upload", data)

    if state.logging_service:
        from core.logging_service import LogCategory, LogLevel
        await state.logging_service.log(
            category=LogCategory.APP, level=LogLevel.INFO,
            message=f"File uploaded: '{file.filename}' ({len(data)} bytes)",
            source="uploads.upload_file", organization_id=account["organization_id"],
            metadata={"object_id": object_id, "filename": file.filename, "size": len(data)}
        )

    return {
        "object_id": object_id,
        "filename": file.filename,
        "size": len(data),
        # Returned so the frontend can drop it straight into a connector's
        # file_content config field — same convention CSV/Excel connectors
        # already documented, just populated by upload instead of hand-typed.
        "content_base64": base64.b64encode(data).decode("ascii"),
    }


@router.get("/{object_id}/preview")
async def preview_upload(object_id: str, account: dict = Depends(get_current_account)):
    """
    The Data Viewer's data source: sniffs the uploaded file's type and
    returns a structured preview — table rows for CSV/Excel, extracted
    text + NLP-detected entities for PDF/email, raw text as a fallback.
    """
    result = state.storage_service.get_with_filename(account["organization_id"], object_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    data, filename = result
    name_lower = filename.lower()

    if name_lower.endswith(".csv"):
        return _preview_csv(data)
    if name_lower.endswith((".xlsx", ".xls")):
        return _preview_excel(data)
    if name_lower.endswith(".pdf"):
        return await _preview_document(data, kind="pdf")
    if name_lower.endswith(".eml"):
        return await _preview_document(data, kind="eml")

    try:
        text = data.decode("utf-8")
        return {"type": "text", "text_preview": text[:5000], "truncated": len(text) > 5000}
    except UnicodeDecodeError:
        return {"type": "binary", "message": f"'{filename}' is a binary file FRIS doesn't have a preview renderer for yet."}


def _preview_csv(data: bytes, max_rows: int = 50) -> Dict[str, Any]:
    text = data.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {"type": "table", "columns": [], "rows": [], "total_rows": 0}
    columns = rows[0]
    data_rows = rows[1:max_rows + 1]
    return {"type": "table", "columns": columns, "rows": data_rows, "total_rows": len(rows) - 1}


def _preview_excel(data: bytes, max_rows: int = 50) -> Dict[str, Any]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {"type": "table", "columns": [], "rows": [], "total_rows": 0, "sheets": wb.sheetnames}
    columns = [str(c) if c is not None else "" for c in rows[0]]
    data_rows = [[("" if c is None else str(c)) for c in r] for r in rows[1:max_rows + 1]]
    return {"type": "table", "columns": columns, "rows": data_rows, "total_rows": len(rows) - 1, "sheets": wb.sheetnames}


async def _preview_document(data: bytes, kind: str) -> Dict[str, Any]:
    from connectors.documents import DocumentConnector, _get_nlp_service
    from connectors.base import ConnectorConfig, ConnectorType

    cfg = ConnectorConfig(
        connector_id="preview", connector_type=ConnectorType.DOCUMENT, name="preview",
        config={"file_content": base64.b64encode(data).decode("ascii"), "file_name": f"preview.{kind}"}
    )
    connector = DocumentConnector(cfg)
    text = connector._extract_text(data)
    entities = _get_nlp_service().extract_entities(text)

    return {
        "type": "document",
        "text_preview": text[:5000],
        "truncated": len(text) > 5000,
        "entities": entities,
        "nlp_backend": _get_nlp_service().backend,
    }
