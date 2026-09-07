"""Shared helpers for M8 backend tests (never collected by pytest).

Rules: every Vault is a tmp_path fixture or a fresh throwaway directory,
every AI adapter is a fake, no real oMLX/network/user Vault is touched, and
all schedule timing uses a fake clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from server.scheduler.clock import iso_utc


class FakeClock:
    """Injectable deterministic clock; tests advance it explicitly."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)

    def now_utc(self) -> datetime:
        return self.now

    def now_iso(self) -> str:
        return iso_utc(self.now)

    def now_tz(self, timezone: str) -> datetime:
        from zoneinfo import ZoneInfo

        return self.now.astimezone(ZoneInfo(timezone))

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)

    def set(self, value: datetime) -> None:
        self.now = value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass
class FakeBackend:
    """Recording backend: no threads, callbacks fire synchronously."""

    name: str = "fake"
    fired: list[str] = None  # type: ignore[assignment]
    started: bool = False
    stopped: int = 0
    degraded_reason: str | None = None
    _callbacks: dict[str, object] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.fired = []
        self._callbacks = {}

    def add(self, definition, callback) -> None:
        self._callbacks[definition.job_id] = callback

    def remove(self, job_id: str) -> None:
        self._callbacks.pop(job_id, None)

    def start(self) -> None:
        self.started = True

    def stop(self, *, wait: bool = True, timeout: float = 5.0) -> None:
        self.stopped += 1
        self.started = False

    def status(self) -> dict:
        return {
            "running": self.started,
            "backend": self.name,
            "available": True,
            "degraded_reason": self.degraded_reason,
            "jobs": [
                {"job_id": job_id, "enabled": True, "next_run_at": None}
                for job_id in sorted(self._callbacks)
            ],
        }

    def registered_ids(self) -> list[str]:
        return sorted(self._callbacks)

    def fire(self, job_id: str) -> None:
        self.fired.append(job_id)
        callback = self._callbacks[job_id]
        callback()  # type: ignore[call-arg]

    def run_now(self, job_id: str):
        self.fire(job_id)
        from server.scheduler.models import RunHandle

        return RunHandle(run_id=f"{job_id}:manual", status="queued")


def make_vault(root, files: dict[str, bytes]):
    import m7support as m7

    return m7.make_vault(root, files)


def make_history(root):
    import m7support as m7

    return m7.make_history(root)


def make_policy(*, level2_auto=()):
    import m7support as m7

    from server.actions.schemas import ActionType

    def _as_action(item: object) -> ActionType:
        return ActionType(item) if isinstance(item, str) else item  # type: ignore[arg-type]

    return m7.make_policy(level2_auto=tuple(_as_action(item) for item in level2_auto))


def make_service(vault, *, history=None, policy=None, adapter=None, **kwargs):
    import m7support as m7

    return m7.make_service(
        vault,
        history=history,
        policy=policy,
        adapter=adapter,
        **kwargs,
    )


class FakeAdapter:
    """One-canned-answer chat adapter; records calls and list_models."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.chat_calls = 0

    async def list_models(self):
        from server.ai.schemas import AICapabilities, DiscoveredModel

        return [DiscoveredModel(id="mock-qwen", capabilities=AICapabilities(chat=True))]

    async def chat(self, **kwargs):
        from server.ai.adapters.base import ChatResult

        self.chat_calls += 1
        return ChatResult(content=self.content, model="mock-qwen")
