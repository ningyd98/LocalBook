"""Bounded OpenAI-compatible HTTP adapter (the sole workflow HTTP boundary)."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx

from ..errors import AIAdapterError, CapabilityUnavailable
from ..schemas import AICapabilities, DiscoveredModel
from .base import ChatResult


def _messages_with_schema(
    messages: list[dict[str, str]], response_schema: dict[str, object]
) -> list[dict[str, str]]:
    """Keep structured-output constraints when a provider rejects json_schema.

    OpenAI-compatible services vary in their supported ``response_format``
    modes.  A prompt-only retry must still tell the model which fields the
    server will validate; otherwise valid JSON with the wrong shape is returned
    and every structured workflow fails locally.
    """
    schema = json.dumps(response_schema, ensure_ascii=False, sort_keys=True)
    instruction = (
        "Return only one valid JSON object matching this JSON Schema exactly. "
        "Include all required properties, do not add properties, and do not use "
        "Markdown fences. JSON Schema:\n"
        f"{schema}"
    )
    result = [dict(message) for message in messages]
    system = next((message for message in result if message.get("role") == "system"), None)
    if system is None:
        result.insert(0, {"role": "system", "content": instruction})
    else:
        system["content"] = f"{system.get('content', '')}\n\n{instruction}"
    return result


def _error_kind(status_code: int) -> str:
    """Map an HTTP failure to the adapter's stable error kind.

    404 stays "model_not_found"; 401/403 are an explicit "auth_error" so the
    settings UI can tell a wrong or missing key from a generic HTTP failure
    (PLAN-PROVIDERS §4).
    """
    if status_code == 404:
        return "model_not_found"
    if status_code in {401, 403}:
        return "auth_error"
    return "http_error"


class OpenAICompatibleAdapter:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 20,
        connect_timeout_seconds: float = 0.5,
        max_response_bytes: int = 1_000_000,
        trust_env: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip() or None
        self.transport = transport
        self.timeout = httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds)
        self.max_bytes = max_response_bytes
        # Shell proxies are opt-in (see ``AISettings.use_env_proxy``).
        self.trust_env = bool(trust_env)
        try:
            host = (urlsplit(self.base_url).hostname or "").lower()
        except ValueError:
            host = ""
        # Chat Completions uses max_tokens on most compatible servers. OpenAI's
        # first-party endpoint uses max_completion_tokens for current models.
        self.max_tokens_field = (
            "max_completion_tokens"
            if host == "api.openai.com"
            else "max_tokens"
        )
        self.is_deepseek = host == "api.deepseek.com"

    @property
    def headers(self) -> dict[str, str]:
        """Bearer auth only when a key is configured (never an empty header)."""
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def _request(self, method: str, path: str, **kwargs):
        headers = {**self.headers, **(kwargs.pop("headers", None) or {})}
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self.transport,
                follow_redirects=False,
                trust_env=self.trust_env,
            ) as c:
                r = await c.request(
                    method, self.base_url + path, headers=headers, **kwargs
                )
                if r.status_code >= 400:
                    raise AIAdapterError(
                        _error_kind(r.status_code),
                        http_status=r.status_code,
                    )
                if len(r.content) > self.max_bytes:
                    raise AIAdapterError("invalid_response")
                return r.json()
        except AIAdapterError:
            raise
        except httpx.TimeoutException as e:
            raise AIAdapterError("timeout") from e
        except (httpx.ConnectError, httpx.HTTPError, OSError) as e:
            raise AIAdapterError("offline") from e
        except (ValueError, UnicodeError) as e:
            raise AIAdapterError("invalid_response") from e

    async def list_models(self):
        payload = await self._request("GET", "/models")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise AIAdapterError("invalid_response")
        result = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise AIAdapterError("invalid_response")
            caps = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
            result.append(
                DiscoveredModel(
                    id=item["id"],
                    owned_by=item.get("owned_by")
                    if isinstance(item.get("owned_by"), str)
                    else None,
                    capabilities=AICapabilities(
                        **{
                            k: v
                            for k, v in caps.items()
                            if k in {"chat", "embedding", "rerank"} and isinstance(v, bool)
                        }
                    ),
                )
            )
        return result

    async def chat(
        self,
        *,
        model,
        messages,
        temperature,
        response_schema,
        timeout_seconds,
        max_output_tokens=1200,
    ):
        body = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            self.max_tokens_field: max_output_tokens,
        }
        if self.is_deepseek:
            # Every LocalBook workflow has a bounded final-answer budget.
            # DeepSeek's default thinking can consume it before any answer,
            # including on the RAG path that does not require a JSON schema.
            body["thinking"] = {"type": "disabled"}
        if response_schema:
            if self.is_deepseek:
                # DeepSeek Chat Completions supports json_object, not the
                # json_schema response format. Structured workflows still need
                # the schema in the prompt for local validation.
                body["messages"] = _messages_with_schema(body["messages"], response_schema)
                body["response_format"] = {"type": "json_object"}
            else:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": response_schema, "strict": True},
                }
        try:
            payload = await self._request("POST", "/chat/completions", json=body)
        except AIAdapterError as exc:
            if (
                exc.kind == "http_error"
                and exc.http_status == 400
                and "response_format" in body
            ):
                # Prefer JSON mode where the provider supports it. The schema
                # remains in the system prompt in either fallback, so the
                # service can still validate one stable output contract.
                if self.is_deepseek:
                    # JSON mode was already the first attempt for DeepSeek.
                    # Retry only once without response_format.
                    fallback = {
                        key: value for key, value in body.items() if key != "response_format"
                    }
                    payload = await self._request("POST", "/chat/completions", json=fallback)
                else:
                    fallback = {
                        **body,
                        "messages": _messages_with_schema(body["messages"], response_schema),
                        "response_format": {"type": "json_object"},
                    }
                    try:
                        payload = await self._request(
                            "POST", "/chat/completions", json=fallback
                        )
                    except AIAdapterError as fallback_error:
                        if (
                            fallback_error.kind != "http_error"
                            or fallback_error.http_status != 400
                        ):
                            raise
                        # Older compatible servers may not support JSON mode at all.
                        plain_json = {
                            key: value
                            for key, value in fallback.items()
                            if key != "response_format"
                        }
                        payload = await self._request(
                            "POST", "/chat/completions", json=plain_json
                        )
            else:
                raise
        try:
            choice = payload["choices"][0]
            if not isinstance(choice, dict):
                raise AIAdapterError("invalid_response")
            if choice.get("finish_reason") in {
                "length", "content_filter", "aborted", "insufficient_system_resource"
            }:
                raise AIAdapterError("invalid_response")
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise AIAdapterError("invalid_response") from e
        if not isinstance(content, str) or not content.strip():
            raise AIAdapterError("invalid_response")
        return ChatResult(content=content, model=str(payload.get("model") or model))

    async def embed(self, *, model, inputs, timeout_seconds):
        raise CapabilityUnavailable()

    async def rerank(self, *, model, query, documents, timeout_seconds):
        raise CapabilityUnavailable()
