"""
FRIS Account Service
Identity + tenancy: email/password accounts, per-account API keys, and the
organization (tenant) each account belongs to.

Every account belongs to exactly one organization. Organizations are the
tenant boundary: every connector, log entry, and fused graph entity is
scoped to an organization_id, and no query path should ever be able to
return data across organizations. Roles (owner/admin/member/viewer) are
scoped within an organization and gate mutating/administrative endpoints
via api/deps.py's require_role dependency.

Mirrors LoggingService/ConnectorManager conventions — shares the control DB
pool, imperative CREATE TABLE IF NOT EXISTS at startup, no ORM.
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg
import jwt
from passlib.context import CryptContext

from config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DEFAULT_ACCOUNT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_ACCOUNT_EMAIL = "default@fris.local"
DEFAULT_ORGANIZATION_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
DEFAULT_ORGANIZATION_NAME = "Default Organization"

API_KEY_PREFIX = "fris_sk_"

# First account in an organization is its owner; every later role must be
# one of these. Ordered weakest-to-strongest for convenience.
ROLES = ["viewer", "member", "admin", "owner"]
DEFAULT_ROLE = "owner"


class AccountService:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def ensure_tables(self):
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS organizations (
                    id UUID PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    plan VARCHAR(50) NOT NULL DEFAULT 'trial'
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id UUID PRIMARY KEY,
                    email VARCHAR(320) UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    name VARCHAR(255),
                    created_at TIMESTAMPTZ DEFAULT now(),
                    status VARCHAR(30) NOT NULL DEFAULT 'active'
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id UUID PRIMARY KEY,
                    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    name VARCHAR(255) NOT NULL,
                    key_prefix VARCHAR(20) NOT NULL,
                    key_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    last_used_at TIMESTAMPTZ,
                    revoked BOOLEAN NOT NULL DEFAULT false
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS api_keys_account_idx ON api_keys (account_id)")

            # Tenancy + RBAC columns on accounts. Nullable + backfilled below
            # so existing single-user dev data keeps working.
            await conn.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS organization_id UUID")
            await conn.execute(f"ALTER TABLE accounts ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT '{DEFAULT_ROLE}'")
            await conn.execute("CREATE INDEX IF NOT EXISTS accounts_organization_idx ON accounts (organization_id)")

            # Tenant attribution columns on pre-existing tables. Nullable +
            # backfilled to the default organization so single-user dev/
            # existing data keeps working, and every graph/log/connector
            # read path can filter on organization_id going forward.
            await conn.execute("ALTER TABLE connectors ADD COLUMN IF NOT EXISTS account_id UUID")
            await conn.execute("ALTER TABLE connectors ADD COLUMN IF NOT EXISTS organization_id UUID")
            await conn.execute("ALTER TABLE logs ADD COLUMN IF NOT EXISTS account_id UUID")
            await conn.execute("ALTER TABLE logs ADD COLUMN IF NOT EXISTS organization_id UUID")
            await conn.execute("CREATE INDEX IF NOT EXISTS connectors_organization_idx ON connectors (organization_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS logs_organization_idx ON logs (organization_id)")

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS invites (
                    id UUID PRIMARY KEY,
                    organization_id UUID NOT NULL,
                    email VARCHAR(320) NOT NULL,
                    role VARCHAR(20) NOT NULL,
                    token VARCHAR(64) UNIQUE NOT NULL,
                    invited_by UUID NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    expires_at TIMESTAMPTZ NOT NULL,
                    accepted_at TIMESTAMPTZ
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS invites_organization_idx ON invites (organization_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS invites_token_idx ON invites (token)")

        await self._ensure_default_account()

    async def _ensure_default_account(self):
        async with self._pool.acquire() as conn:
            existing_org = await conn.fetchrow("SELECT id FROM organizations WHERE id = $1", DEFAULT_ORGANIZATION_ID)
            if not existing_org:
                await conn.execute(
                    "INSERT INTO organizations (id, name, plan) VALUES ($1, $2, 'trial')",
                    DEFAULT_ORGANIZATION_ID, DEFAULT_ORGANIZATION_NAME
                )

            existing = await conn.fetchrow("SELECT id FROM accounts WHERE id = $1", DEFAULT_ACCOUNT_ID)
            if not existing:
                await conn.execute(
                    "INSERT INTO accounts (id, email, password_hash, name, status, organization_id, role) "
                    "VALUES ($1, $2, $3, $4, 'active', $5, $6)",
                    DEFAULT_ACCOUNT_ID, DEFAULT_ACCOUNT_EMAIL,
                    _pwd_context.hash(secrets.token_urlsafe(32)), "Default Account",
                    DEFAULT_ORGANIZATION_ID, DEFAULT_ROLE
                )
            # Backfill any rows still unattributed (pre-existing data from before
            # accounts/organizations existed).
            await conn.execute(
                "UPDATE accounts SET organization_id = $1 WHERE organization_id IS NULL AND id != $2",
                DEFAULT_ORGANIZATION_ID, DEFAULT_ACCOUNT_ID
            )
            await conn.execute(
                "UPDATE connectors SET account_id = $1 WHERE account_id IS NULL", DEFAULT_ACCOUNT_ID
            )
            await conn.execute(
                "UPDATE connectors c SET organization_id = a.organization_id "
                "FROM accounts a WHERE c.account_id = a.id AND c.organization_id IS NULL"
            )
            await conn.execute(
                "UPDATE logs SET account_id = $1 WHERE account_id IS NULL", DEFAULT_ACCOUNT_ID
            )
            await conn.execute(
                "UPDATE logs l SET organization_id = a.organization_id "
                "FROM accounts a WHERE l.account_id = a.id AND l.organization_id IS NULL"
            )

    # ---------- Accounts ----------

    async def create_account(self, email: str, password: str, name: Optional[str] = None) -> Dict[str, Any]:
        """
        Every new signup gets its own organization (tenant) and becomes
        that organization's owner. To add teammates into that same
        organization instead of each getting a separate one, use
        create_invite() + accept_invite() below.
        """
        account_id = uuid.uuid4()
        organization_id = uuid.uuid4()
        password_hash = _pwd_context.hash(password)
        org_name = f"{name or email.split('@')[0]}'s Organization"
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow("SELECT id FROM accounts WHERE email = $1", email.lower())
                if existing:
                    raise ValueError("An account with this email already exists")
                await conn.execute(
                    "INSERT INTO organizations (id, name) VALUES ($1, $2)",
                    organization_id, org_name
                )
                await conn.execute(
                    "INSERT INTO accounts (id, email, password_hash, name, organization_id, role) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    account_id, email.lower(), password_hash, name, organization_id, DEFAULT_ROLE
                )
        return {
            "id": str(account_id), "email": email.lower(), "name": name,
            "organization_id": str(organization_id), "role": DEFAULT_ROLE
        }

    async def authenticate(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, email, password_hash, name, status, organization_id, role "
                "FROM accounts WHERE email = $1", email.lower()
            )
        if not row or row["status"] != "active":
            return None
        if not _pwd_context.verify(password, row["password_hash"]):
            return None
        return {
            "id": str(row["id"]), "email": row["email"], "name": row["name"],
            "organization_id": str(row["organization_id"]) if row["organization_id"] else None,
            "role": row["role"]
        }

    async def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, email, name, created_at, status, organization_id, role "
                "FROM accounts WHERE id = $1", uuid.UUID(account_id)
            )
        if not row:
            return None
        d = dict(row)
        d["organization_id"] = str(d["organization_id"]) if d["organization_id"] else None
        return d

    # ---------- JWT sessions ----------

    def create_jwt(self, account_id: str) -> str:
        payload = {
            "sub": account_id,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRES_MINUTES),
        }
        return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    def decode_jwt(self, token: str) -> Optional[str]:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
            return payload.get("sub")
        except jwt.PyJWTError:
            return None

    # ---------- API keys ----------

    async def create_api_key(self, account_id: str, name: str) -> Dict[str, Any]:
        raw_key = API_KEY_PREFIX + secrets.token_urlsafe(32)
        key_id = uuid.uuid4()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO api_keys (id, account_id, name, key_prefix, key_hash) VALUES ($1, $2, $3, $4, $5)",
                key_id, uuid.UUID(account_id), name, raw_key[:12], _pwd_context.hash(raw_key)
            )
        # Plaintext key is returned exactly once — never stored, never retrievable again.
        return {"id": str(key_id), "name": name, "key": raw_key, "key_prefix": raw_key[:12]}

    async def list_api_keys(self, account_id: str) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, key_prefix, created_at, last_used_at, revoked "
                "FROM api_keys WHERE account_id = $1 ORDER BY created_at DESC",
                uuid.UUID(account_id)
            )
        return [dict(r) for r in rows]

    async def revoke_api_key(self, account_id: str, key_id: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE api_keys SET revoked = true WHERE id = $1 AND account_id = $2",
                uuid.UUID(key_id), uuid.UUID(account_id)
            )

    async def authenticate_api_key(self, raw_key: str) -> Optional[Dict[str, Any]]:
        """
        API keys are bcrypt-hashed at rest, so lookup can't be a direct
        equality query — narrow candidates by the stored prefix first
        (cheap, indexed via key_prefix), then verify the hash.
        """
        if not raw_key.startswith(API_KEY_PREFIX):
            return None
        prefix = raw_key[:12]
        async with self._pool.acquire() as conn:
            candidates = await conn.fetch(
                "SELECT id, account_id, key_hash FROM api_keys WHERE key_prefix = $1 AND revoked = false",
                prefix
            )
            for row in candidates:
                if _pwd_context.verify(raw_key, row["key_hash"]):
                    await conn.execute(
                        "UPDATE api_keys SET last_used_at = now() WHERE id = $1", row["id"]
                    )
                    return await self.get_account(str(row["account_id"]))
        return None

    # ---------- Team management (invites, roster, roles) ----------
    # No SMTP is wired into this deployment (matches the rest of the
    # codebase's founder-led, manually-shared conventions — e.g. billing has
    # no payment processor either). create_invite returns the invite link
    # directly for the owner/admin to copy and share; there's no email send.

    INVITE_EXPIRY_DAYS = 7

    async def create_invite(self, organization_id: str, email: str, role: str, invited_by: str) -> Dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"Invalid role '{role}'. Must be one of {ROLES}")

        async with self._pool.acquire() as conn:
            existing_account = await conn.fetchrow("SELECT id FROM accounts WHERE email = $1", email.lower())
            if existing_account:
                raise ValueError("An account with this email already exists")

            invite_id = uuid.uuid4()
            token = secrets.token_urlsafe(32)
            expires_at = datetime.now(timezone.utc) + timedelta(days=self.INVITE_EXPIRY_DAYS)
            await conn.execute(
                "INSERT INTO invites (id, organization_id, email, role, token, invited_by, expires_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                invite_id, uuid.UUID(organization_id), email.lower(), role, token,
                uuid.UUID(invited_by), expires_at
            )
        return {
            "id": str(invite_id), "email": email.lower(), "role": role,
            "token": token, "expires_at": expires_at.isoformat()
        }

    async def list_invites(self, organization_id: str) -> List[Dict[str, Any]]:
        """Pending (unaccepted, unexpired) invites for this organization — the roster page shows these alongside active members."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, email, role, created_at, expires_at FROM invites "
                "WHERE organization_id = $1 AND accepted_at IS NULL AND expires_at > now() "
                "ORDER BY created_at DESC",
                uuid.UUID(organization_id)
            )
        return [dict(r) for r in rows]

    async def revoke_invite(self, organization_id: str, invite_id: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM invites WHERE id = $1 AND organization_id = $2 AND accepted_at IS NULL",
                uuid.UUID(invite_id), uuid.UUID(organization_id)
            )
        return result != "DELETE 0"

    async def accept_invite(self, token: str, password: str, name: Optional[str] = None) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                invite = await conn.fetchrow(
                    "SELECT id, organization_id, email, role FROM invites "
                    "WHERE token = $1 AND accepted_at IS NULL AND expires_at > now()",
                    token
                )
                if not invite:
                    raise ValueError("This invite is invalid, expired, or already used")

                existing = await conn.fetchrow("SELECT id FROM accounts WHERE email = $1", invite["email"])
                if existing:
                    raise ValueError("An account with this email already exists")

                account_id = uuid.uuid4()
                await conn.execute(
                    "INSERT INTO accounts (id, email, password_hash, name, organization_id, role) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    account_id, invite["email"], _pwd_context.hash(password), name,
                    invite["organization_id"], invite["role"]
                )
                await conn.execute("UPDATE invites SET accepted_at = now() WHERE id = $1", invite["id"])

        return {
            "id": str(account_id), "email": invite["email"], "name": name,
            "organization_id": str(invite["organization_id"]), "role": invite["role"]
        }

    async def list_team(self, organization_id: str) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, email, name, role, status, created_at FROM accounts "
                "WHERE organization_id = $1 ORDER BY created_at ASC",
                uuid.UUID(organization_id)
            )
        return [dict(r) for r in rows]

    async def update_member_role(self, organization_id: str, account_id: str, new_role: str) -> Dict[str, Any]:
        if new_role not in ROLES:
            raise ValueError(f"Invalid role '{new_role}'. Must be one of {ROLES}")

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                target = await conn.fetchrow(
                    "SELECT id, role FROM accounts WHERE id = $1 AND organization_id = $2",
                    uuid.UUID(account_id), uuid.UUID(organization_id)
                )
                if not target:
                    raise ValueError("No such account in this organization")

                if target["role"] == "owner" and new_role != "owner":
                    owner_count = await conn.fetchval(
                        "SELECT count(*) FROM accounts WHERE organization_id = $1 AND role = 'owner' AND status = 'active'",
                        uuid.UUID(organization_id)
                    )
                    if owner_count <= 1:
                        raise ValueError("Cannot demote the organization's only owner — promote another member first")

                await conn.execute(
                    "UPDATE accounts SET role = $1 WHERE id = $2", new_role, uuid.UUID(account_id)
                )
        return {"id": account_id, "role": new_role}

    async def remove_member(self, organization_id: str, account_id: str) -> bool:
        """
        Soft-delete: sets status='removed' rather than deleting the row, so
        historical attribution (which account synced this connector, wrote
        this log entry) stays meaningful and authenticate() already refuses
        login for any non-'active' account.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                target = await conn.fetchrow(
                    "SELECT id, role FROM accounts WHERE id = $1 AND organization_id = $2",
                    uuid.UUID(account_id), uuid.UUID(organization_id)
                )
                if not target:
                    return False
                if target["role"] == "owner":
                    owner_count = await conn.fetchval(
                        "SELECT count(*) FROM accounts WHERE organization_id = $1 AND role = 'owner' AND status = 'active'",
                        uuid.UUID(organization_id)
                    )
                    if owner_count <= 1:
                        raise ValueError("Cannot remove the organization's only owner")

                result = await conn.execute(
                    "UPDATE accounts SET status = 'removed' WHERE id = $1", uuid.UUID(account_id)
                )
        return result != "UPDATE 0"
