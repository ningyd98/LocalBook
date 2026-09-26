"""Bounded, opt-in SearXNG JSON search for Reader web questions."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from server.config import WebSearchSettings


class WebSearchUnavailable(Exception):
    """The configured search service did not return usable JSON results."""


@dataclass(frozen=True, slots=True)
class WebSearchHit:
    title: str
    url: str
    snippet: str


def _plain_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    # SearXNG snippets may contain HTML emphasis. Keep only visible text in
    # both model context and the returned citation card.
    visible = html.unescape(re.sub(r"<[^>]*>", " ", value))
    return " ".join(visible.split())[:limit]


def _http_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return value


class SearXNGSearchService:
    def __init__(self, settings: WebSearchSettings) -> None:
        base = settings.base_url
        if not base:
            raise WebSearchUnavailable("Web search is not configured")
        try:
            parsed = urlsplit(base)
        except ValueError as exc:
            raise WebSearchUnavailable("Web search endpoint is invalid") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise WebSearchUnavailable("Web search endpoint is invalid")
        self.endpoint = base.rstrip("/") + "/search"
        self.timeout = settings.timeout_seconds
        self.max_response_bytes = settings.max_response_bytes

    async def search(self, query: str, *, limit: int = 4) -> list[WebSearchHit]:
        if not query.strip():
            return []
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "GET",
                    self.endpoint,
                    params={"q": query[:500], "format": "json"},
                    headers={"Accept": "application/json"},
                ) as response:
                    if response.status_code != 200:
                        raise WebSearchUnavailable("Web search endpoint rejected the request")
                    parts: list[bytes] = []
                    size = 0
                    async for part in response.aiter_bytes():
                        size += len(part)
                        if size > self.max_response_bytes:
                            raise WebSearchUnavailable("Web search response is too large")
                        parts.append(part)
            payload = json.loads(b"".join(parts))
        except WebSearchUnavailable:
            raise
        except (httpx.HTTPError, OSError, ValueError, UnicodeError) as exc:
            raise WebSearchUnavailable("Web search service is unavailable") from exc

        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            raise WebSearchUnavailable("Web search response has no results list")
        hits: list[WebSearchHit] = []
        seen: set[str] = set()
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            url = _http_url(raw.get("url"))
            if not url or url in seen:
                continue
            title = _plain_text(raw.get("title"), 140) or url
            snippet = _plain_text(raw.get("content"), 450)
            if not snippet:
                continue
            seen.add(url)
            hits.append(WebSearchHit(title=title, url=url, snippet=snippet))
            if len(hits) >= limit:
                break
        return hits
