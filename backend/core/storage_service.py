"""
FRIS Storage Service
Tenant-scoped object storage for uploaded files (CSV/Excel/PDF/email —
whatever the browser-upload connectors introduced in a later phase write
here), plus encryption at rest for anything written through it.

Every object lives under a per-organization prefix, so a bug that guesses
or enumerates object keys still can't reach another tenant's files — the
same tenant boundary enforced on the graph (fusion_engine.py) and control
plane (account_service.py) extends to file storage.

Local filesystem backend today (encrypted at rest via Fernet, same key
material as llm_credential_service.py's BYO-LLM-credential encryption) so
self-hosted/air-gapped deployments have zero external dependency. The
interface is written narrow enough that swapping in an S3-compatible
backend (real S3, or MinIO for on-prem parity) for hosted/SaaS deployments
is a new class implementing the same three methods, not a rewrite of
callers.
"""

import os
import re
import uuid
from pathlib import Path
from typing import BinaryIO, Optional

from cryptography.fernet import Fernet

from config import settings

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _sanitize_filename(name: str) -> str:
    """Strip path separators and anything not alphanumeric/dot/dash/underscore
    so a malicious filename can't escape the tenant's storage directory."""
    base = os.path.basename(name)
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or "file"
    return cleaned[:200]


class StorageService:
    """
    Local-disk, tenant-scoped, encrypted-at-rest object storage.

    Layout: {root}/{organization_id}/{object_id}__{sanitized_filename}
    The object_id (a UUID) is what callers store as the durable reference;
    the filename suffix is just for human-readable listing/debugging, never
    trusted for lookups.
    """

    def __init__(self, root: Optional[str] = None, encryption_key: Optional[str] = None):
        self.root = Path(root or os.getenv("FRIS_STORAGE_ROOT", "/app/data/tenant-storage"))
        self.root.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet((encryption_key or settings.ENCRYPTION_KEY).encode()
                               if isinstance(encryption_key or settings.ENCRYPTION_KEY, str)
                               else (encryption_key or settings.ENCRYPTION_KEY))

    def _tenant_dir(self, organization_id: str) -> Path:
        # organization_id is always a UUID string from account_service, never
        # user-supplied free text, so no extra sanitization needed here —
        # but validate the shape defensively since this becomes a directory name.
        uuid.UUID(organization_id)
        d = self.root / organization_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def put(self, organization_id: str, filename: str, data: bytes) -> str:
        """Encrypts and writes data under this tenant's directory. Returns the object_id to store as a reference."""
        object_id = str(uuid.uuid4())
        safe_name = _sanitize_filename(filename)
        path = self._tenant_dir(organization_id) / f"{object_id}__{safe_name}"
        path.write_bytes(self._fernet.encrypt(data))
        return object_id

    def get(self, organization_id: str, object_id: str) -> Optional[bytes]:
        """Reads and decrypts an object. Returns None if it doesn't exist under this tenant's directory —
        an object_id belonging to another organization is indistinguishable from a missing one."""
        result = self.get_with_filename(organization_id, object_id)
        return result[0] if result else None

    def get_with_filename(self, organization_id: str, object_id: str):
        """Same as get(), but also returns the original filename — used by the
        Data Viewer to sniff file type (.csv/.xlsx/.pdf/.eml) for rendering."""
        uuid.UUID(object_id)  # defensive: reject anything that isn't the ID shape we issued
        matches = list(self._tenant_dir(organization_id).glob(f"{object_id}__*"))
        if not matches:
            return None
        filename = matches[0].name.split("__", 1)[1]
        return self._fernet.decrypt(matches[0].read_bytes()), filename

    def delete(self, organization_id: str, object_id: str) -> bool:
        matches = list(self._tenant_dir(organization_id).glob(f"{object_id}__*"))
        for m in matches:
            m.unlink()
        return bool(matches)
