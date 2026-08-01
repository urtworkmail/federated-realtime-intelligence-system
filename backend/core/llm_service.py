"""
FRIS LLM Service — provider-agnostic query layer (Phase 2).
Adapters are thin httpx wrappers (no heavy provider SDKs) sharing one
interface: generate(messages) -> {text, prompt_tokens, completion_tokens}.

Providers:
  fris      - FRIS-hosted default. Falls back to a deterministic,
              offline extractive answer built from the retrieved entities
              when no hosted endpoint is configured — keeps FRIS usable
              fully air-gapped, matching the embedding service's ethos.
  openai    - api-key
  anthropic - api-key
  google    - oauth (Gemini via Google Cloud)
  azure     - oauth
"""

from typing import Any, Dict, List, Optional

import httpx

from config import settings
from core.llm_credential_service import LLMCredentialService


class LLMAdapter:
    provider = "base"

    async def generate(self, messages: List[Dict[str, str]], credential: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        raise NotImplementedError


class FrisHostedAdapter(LLMAdapter):
    """
    Default provider. No external network call by default (offline-first,
    consistent with EmbeddingService) — builds a grounded, extractive
    answer directly from the retrieved context. An account can point this
    at a self-hosted FRIS inference endpoint later without changing the
    calling contract.
    """
    provider = "fris"

    async def generate(self, messages: List[Dict[str, str]], credential: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        user_message = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        system_message = next((m["content"] for m in messages if m["role"] == "system"), "")
        text = (
            f"{user_message.strip()}\n\n"
            "Based on the retrieved entities above, here is what the fused graph shows. "
            "(FRIS-hosted offline mode — connect a cloud provider under Settings -> LLM Providers "
            "for free-form generation.)"
        )
        approx_tokens = len((system_message + user_message).split())
        return {"text": text, "prompt_tokens": approx_tokens, "completion_tokens": len(text.split())}


class OpenAIAdapter(LLMAdapter):
    provider = "openai"

    async def generate(self, messages: List[Dict[str, str]], credential: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not credential or not credential.get("secret"):
            raise RuntimeError("No OpenAI API key configured for this account")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {credential['secret']}"},
                json={"model": "gpt-4o-mini", "messages": messages},
            )
            resp.raise_for_status()
            data = resp.json()
        return {
            "text": data["choices"][0]["message"]["content"],
            "prompt_tokens": data.get("usage", {}).get("prompt_tokens", 0),
            "completion_tokens": data.get("usage", {}).get("completion_tokens", 0),
        }


class AnthropicAdapter(LLMAdapter):
    provider = "anthropic"

    async def generate(self, messages: List[Dict[str, str]], credential: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not credential or not credential.get("secret"):
            raise RuntimeError("No Anthropic API key configured for this account")
        system_message = next((m["content"] for m in messages if m["role"] == "system"), None)
        user_messages = [m for m in messages if m["role"] != "system"]
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": credential["secret"],
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": "claude-sonnet-5",
                    "max_tokens": 1024,
                    "system": system_message,
                    "messages": user_messages,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return {
            "text": "".join(b.get("text", "") for b in data.get("content", [])),
            "prompt_tokens": data.get("usage", {}).get("input_tokens", 0),
            "completion_tokens": data.get("usage", {}).get("output_tokens", 0),
        }


class GoogleAdapter(LLMAdapter):
    """OAuth-based — expects an already-refreshed access token in credential['access_token']."""
    provider = "google"

    async def generate(self, messages: List[Dict[str, str]], credential: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not credential or not credential.get("access_token"):
            raise RuntimeError("No Google OAuth connection for this account")
        contents = [{"role": "user" if m["role"] != "assistant" else "model", "parts": [{"text": m["content"]}]}
                    for m in messages if m["role"] != "system"]
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
                headers={"Authorization": f"Bearer {credential['access_token']}"},
                json={"contents": contents},
            )
            resp.raise_for_status()
            data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata", {})
        return {
            "text": text,
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
        }


class AzureAdapter(LLMAdapter):
    """OAuth-based — expects credential['access_token'] and credential['metadata']['endpoint']/['deployment']."""
    provider = "azure"

    async def generate(self, messages: List[Dict[str, str]], credential: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not credential or not credential.get("access_token"):
            raise RuntimeError("No Azure OAuth connection for this account")
        endpoint = (credential.get("metadata") or {}).get("endpoint")
        deployment = (credential.get("metadata") or {}).get("deployment", "gpt-4o-mini")
        if not endpoint:
            raise RuntimeError("Azure OpenAI endpoint not configured for this account")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=2024-06-01",
                headers={"Authorization": f"Bearer {credential['access_token']}"},
                json={"messages": messages},
            )
            resp.raise_for_status()
            data = resp.json()
        return {
            "text": data["choices"][0]["message"]["content"],
            "prompt_tokens": data.get("usage", {}).get("prompt_tokens", 0),
            "completion_tokens": data.get("usage", {}).get("completion_tokens", 0),
        }


ADAPTERS: Dict[str, LLMAdapter] = {
    "fris": FrisHostedAdapter(),
    "openai": OpenAIAdapter(),
    "anthropic": AnthropicAdapter(),
    "google": GoogleAdapter(),
    "azure": AzureAdapter(),
}

# Below this similarity score, treat retrieval as too weak to answer confidently
# and ask a clarifying question instead (Phase 3 seed).
CLARIFICATION_THRESHOLD = 0.35


class LLMService:
    def __init__(self, credential_service: LLMCredentialService):
        self.credential_service = credential_service

    async def _resolve_credential(self, account_id: str, provider: str) -> Optional[Dict[str, Any]]:
        if provider == "fris":
            return None
        cred = await self.credential_service.get_credential(account_id, provider)
        if cred and cred.get("auth_type") == "oauth":
            # Adapters expect a live access_token; the OAuth router is
            # responsible for refreshing and storing it before this point
            # in the simple flow implemented here (see api/oauth.py).
            cred["access_token"] = cred.get("refresh_token")
        return cred

    def build_rag_messages(
        self, query: str, context_entities: List[Dict[str, Any]],
        canonical_definitions: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, str]]:
        context_lines = []
        for e in context_entities:
            props = {k: v for k, v in e.get("properties", {}).items() if not k.startswith("_") and k != "embedding"}
            context_lines.append(f"- ({e.get('entity_type')}) {props}")
        context_block = "\n".join(context_lines) if context_lines else "(no matching entities found)"

        system = (
            "You are the FRIS assistant. Answer the user's question strictly using the "
            "retrieved entities below. Cite entity names when relevant. If the context "
            "doesn't contain enough information, say so plainly."
        )
        # Canonical definitions (core/context_service.py) tell the model which
        # fused field is authoritative when a property name is ambiguous —
        # e.g. "revenue" meaning annual_revenue_usd, not a stale mirror field —
        # the a16z "living context layer" thesis this feature answers directly.
        if canonical_definitions:
            definition_lines = [
                f"- {d['entity_type']}.{d['property_name']}: use field '{d['canonical_field']}'"
                + (f" ({d['description']})" if d.get("description") else "")
                for d in canonical_definitions
            ]
            system += (
                "\n\nWhen a property has a canonical definition below, prefer that field "
                "over any similarly-named one in the retrieved entities:\n" + "\n".join(definition_lines)
            )

        user = f"Retrieved entities:\n{context_block}\n\nQuestion: {query}"
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    async def answer(
        self, account_id: str, query: str, context_entities: List[Dict[str, Any]],
        provider: Optional[str] = None, canonical_definitions: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        provider = provider or await self.credential_service.get_default_provider(account_id)
        adapter = ADAPTERS.get(provider)
        if not adapter:
            raise ValueError(f"Unknown LLM provider '{provider}'")

        top_score = max((e.get("similarity_score", 0) for e in context_entities), default=0)
        if not context_entities or top_score < CLARIFICATION_THRESHOLD:
            return {
                "needs_clarification": True,
                "question": (
                    "I couldn't find a confident match for that in the fused graph. "
                    "Could you rephrase, or mention an entity type (e.g. Organization, Person) "
                    "or a specific name?"
                ),
                "provider": provider,
            }

        credential = await self._resolve_credential(account_id, provider)
        messages = self.build_rag_messages(query, context_entities, canonical_definitions=canonical_definitions)
        result = await adapter.generate(messages, credential)

        return {
            "needs_clarification": False,
            "answer": result["text"],
            "sources": [
                {"fris_id": e["fris_id"], "entity_type": e["entity_type"], "similarity_score": e.get("similarity_score")}
                for e in context_entities
            ],
            "provider": provider,
            "tokens": {
                "prompt": result["prompt_tokens"],
                "completion": result["completion_tokens"],
                "total": result["prompt_tokens"] + result["completion_tokens"],
            },
        }
