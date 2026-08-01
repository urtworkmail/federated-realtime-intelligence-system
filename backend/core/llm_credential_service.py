"""
Per-account LLM provider credentials — API keys (OpenAI, Anthropic, etc.)
and OAuth tokens (Google, Azure — the only providers that require OAuth
rather than a plain key). Secrets are encrypted at rest via utils/crypto.
Mirrors AccountService's table/service conventions.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from utils.crypto import decrypt, encrypt

OAUTH_PROVIDERS = {"google", "azure"}
API_KEY_PROVIDERS = {"fris", "openai", "anthropic"}


class LLMCredentialService:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def ensure_tables(self):
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_credentials (
                    id UUID PRIMARY KEY,
                    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    provider VARCHAR(30) NOT NULL,
                    auth_type VARCHAR(20) NOT NULL,
                    encrypted_secret TEXT,
                    oauth_refresh_token TEXT,
                    oauth_expires_at TIMESTAMPTZ,
                    is_default BOOLEAN NOT NULL DEFAULT false,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE (account_id, provider)
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS llm_credentials_account_idx ON llm_credentials (account_id)")

    async def set_api_key(self, account_id: str, provider: str, api_key: str) -> Dict[str, Any]:
        cred_id = uuid.uuid4()
        encrypted = encrypt(api_key)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO llm_credentials (id, account_id, provider, auth_type, encrypted_secret)
                VALUES ($1, $2, $3, 'apikey', $4)
                ON CONFLICT (account_id, provider)
                DO UPDATE SET auth_type = 'apikey', encrypted_secret = $4, oauth_refresh_token = NULL
                """,
                cred_id, uuid.UUID(account_id), provider, encrypted
            )
        return {"provider": provider, "auth_type": "apikey"}

    async def set_oauth_tokens(
        self, account_id: str, provider: str, refresh_token: str, expires_at: Optional[datetime] = None
    ) -> Dict[str, Any]:
        cred_id = uuid.uuid4()
        encrypted_refresh = encrypt(refresh_token)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO llm_credentials (id, account_id, provider, auth_type, oauth_refresh_token, oauth_expires_at)
                VALUES ($1, $2, $3, 'oauth', $4, $5)
                ON CONFLICT (account_id, provider)
                DO UPDATE SET auth_type = 'oauth', oauth_refresh_token = $4, oauth_expires_at = $5, encrypted_secret = NULL
                """,
                cred_id, uuid.UUID(account_id), provider, encrypted_refresh, expires_at
            )
        return {"provider": provider, "auth_type": "oauth"}

    async def get_credential(self, account_id: str, provider: str) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM llm_credentials WHERE account_id = $1 AND provider = $2",
                uuid.UUID(account_id), provider
            )
        if not row:
            return None
        result = dict(row)
        if result.get("encrypted_secret"):
            result["secret"] = decrypt(result["encrypted_secret"])
        if result.get("oauth_refresh_token"):
            result["refresh_token"] = decrypt(result["oauth_refresh_token"])
        return result

    async def list_providers(self, account_id: str) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT provider, auth_type, is_default, created_at FROM llm_credentials "
                "WHERE account_id = $1 ORDER BY created_at DESC",
                uuid.UUID(account_id)
            )
        return [dict(r) for r in rows]

    async def delete_credential(self, account_id: str, provider: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM llm_credentials WHERE account_id = $1 AND provider = $2",
                uuid.UUID(account_id), provider
            )

    async def set_default(self, account_id: str, provider: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE llm_credentials SET is_default = (provider = $2) WHERE account_id = $1",
                uuid.UUID(account_id), provider
            )

    async def get_default_provider(self, account_id: str) -> str:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT provider FROM llm_credentials WHERE account_id = $1 AND is_default = true",
                uuid.UUID(account_id)
            )
        return row["provider"] if row else "fris"
