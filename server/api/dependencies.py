"""FastAPI dependency wiring for the API services."""

from __future__ import annotations

from fastapi import Request

from server.actions.schemas import ActionType
from server.agents.registry import ToolRegistry
from server.agents.schemas import AcceptJobRequest, JobRequest, UndoJobRequest
from server.agents.service import AgentJobService
from server.agents.tools import ToolContext
from server.agents.workflows import WorkflowPlanner
from server.ai.adapters.openai_compatible import OpenAICompatibleAdapter
from server.ai.service import AIStatusService
from server.ai.workflows import AIWorkflowService
from server.config import Settings
from server.graph.service import GraphService
from server.history.repository import HistoryRepository
from server.history.service import HistoryService
from server.index.service import DerivedIndexService
from server.links.service import LinksService
from server.metadata.service import MetadataService
from server.policies.engine import PolicyEngine
from server.rag.errors import RagUnavailable
from server.rag.service import RagService
from server.scheduler.service import SchedulerService
from server.search.service import SearchService
from server.vault.errors import VaultNotConfigured, VaultUnavailable
from server.vault.service import VaultService
from server.vault.trash import TrashService

__all__ = [
    "AcceptJobRequest",
    "AgentJobService",
    "JobRequest",
    "ToolRegistry",
    "UndoJobRequest",
    "WorkflowPlanner",
]


def _settings_from_app(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = Settings()
        request.app.state.settings = settings
    return settings


def get_ai_status_service(request: Request) -> AIStatusService:
    ai = _settings_from_app(request).ai
    # The applied provider profile (PLAN-PROVIDERS) rides along so /ai/status
    # can name the route it probed.
    return AIStatusService.from_ai_settings(ai if ai.enabled else ai.model_copy(update={"base_url": None}))


def get_ai_workflow_service(request: Request) -> AIWorkflowService:
    settings = _settings_from_app(request).ai
    if not settings.enabled or not settings.base_url:
        from server.ai.errors import AIError, AIErrorCode

        raise AIError(
            AIErrorCode.DISABLED if not settings.enabled else AIErrorCode.NOT_CONFIGURED,
            "AI is not configured",
        )
    vault: VaultService | None = None
    try:
        vault = get_vault_service(request)
    except (VaultNotConfigured, VaultUnavailable):
        pass
    return AIWorkflowService(
        settings,
        OpenAICompatibleAdapter(
            settings.base_url,
            api_key=settings.api_key,
            timeout_seconds=settings.request_timeout_seconds,
            connect_timeout_seconds=settings.connect_timeout_seconds,
            max_response_bytes=settings.max_models_response_bytes,
            trust_env=bool(getattr(settings, "use_env_proxy", False)),
        ),
        vault=vault,
        index=getattr(request.app.state, "index_service", None),
    )


def get_vault_service(request: Request) -> VaultService:
    service = getattr(request.app.state, "vault_service", None)
    if service is not None:
        return service
    lifecycle = getattr(request.app.state, "vault_lifecycle", None)
    if lifecycle is not None and lifecycle.error is not None:
        raise lifecycle.error
    settings = _settings_from_app(request)
    if settings.vault.root is None:
        raise VaultNotConfigured()
    if lifecycle is None:
        from server.vault.lifecycle import VaultLifecycle

        lifecycle = VaultLifecycle(settings.vault, rag=settings.rag)
        request.app.state.vault_lifecycle = lifecycle
    service = lifecycle.startup()
    if service is None:
        raise lifecycle.error or VaultUnavailable()
    request.app.state.vault_service = service
    if lifecycle.index_service is not None:
        request.app.state.index_service = lifecycle.index_service
    return service


def get_trash_service(request: Request) -> TrashService:
    """The recycle bin of the active Vault (soft delete + restore + retention).

    It shares the Vault's mutation lock and safety checks, so a soft delete is
    serialized with every other write and ``.localnote`` stays unreachable
    through the public file API.
    """
    vault = get_vault_service(request)
    service = getattr(request.app.state, "trash_service", None)
    if service is not None and service.vault is vault:
        return service
    settings = _settings_from_app(request)
    service = TrashService(vault, retention_days=settings.vault.trash_retention_days)
    request.app.state.trash_service = service
    return service


def get_index_service(request: Request) -> DerivedIndexService:
    index = getattr(request.app.state, "index_service", None)
    if index is None:
        get_vault_service(request)
        index = getattr(request.app.state, "vault_lifecycle", None)
        if index is not None:
            index = getattr(index, "index_service", None)
    if index is None:
        raise VaultUnavailable()
    return index


def get_rag_stack(request: Request):
    """The M14 RAG stack for the active Vault (None when unavailable/disabled).

    Prefers the lifecycle-owned stack published on ``app.state`` by the runtime,
    and falls back to starting the Vault (which builds the stack) so a
    ``TestClient`` without the lifespan still works. Raises 503 when RAG cannot
    be built at all — RAG is optional, the rest of the API is unaffected.
    """
    stack = getattr(request.app.state, "rag_stack", None)
    if stack is not None:
        return stack
    get_vault_service(request)
    lifecycle = getattr(request.app.state, "vault_lifecycle", None)
    stack = getattr(lifecycle, "rag_stack", None)
    if stack is None:
        raise RagUnavailable("RAG index is unavailable")
    request.app.state.rag_stack = stack
    return stack


def get_rag_service(request: Request) -> RagService:
    """The RAG service with the active chat provider bound for generation."""
    stack = get_rag_stack(request)
    settings = _settings_from_app(request)
    stack.service.set_generator(_rag_generator(settings, request))
    return stack.service


def _rag_generator(settings, request: Request):
    """Bind the configured chat model as the RAG generator (or None)."""
    ai = settings.ai
    if not ai.enabled or not ai.base_url:
        return None
    from ..ai.adapters.base import ChatMessage
    from ..ai.adapters.openai_compatible import OpenAICompatibleAdapter
    from ..ai.capabilities import resolve_chat_model

    adapter = OpenAICompatibleAdapter(
        ai.base_url,
        api_key=ai.api_key,
        timeout_seconds=ai.request_timeout_seconds,
        connect_timeout_seconds=ai.connect_timeout_seconds,
        max_response_bytes=ai.max_models_response_bytes,
        trust_env=bool(getattr(ai, "use_env_proxy", False)),
    )

    async def generate(system_prompt: str, user_prompt: str) -> tuple[str, str]:
        model = resolve_chat_model(
            await adapter.list_models(), ai.chat_model, ai.qwen_match_pattern
        )
        result = await adapter.chat(
            model=model,
            messages=[
                ChatMessage("system", system_prompt),
                ChatMessage("user", user_prompt),
            ],
            temperature=ai.temperature,
            response_schema=None,
            timeout_seconds=ai.request_timeout_seconds,
            max_output_tokens=ai.max_output_tokens,
        )
        return result.content, result.model

    return generate


def get_metadata_service(request: Request) -> MetadataService:
    get_vault_service(request)
    get_index_service(request).assert_ready()
    return MetadataService(get_vault_service(request))


def get_links_service(request: Request) -> LinksService:
    return LinksService(get_index_service(request))


def get_search_service(request: Request) -> SearchService:
    return SearchService(get_index_service(request))


def get_graph_service(request: Request) -> GraphService:
    return GraphService(get_index_service(request), settings=_settings_from_app(request).graph)


def _build_policy_engine(settings: Settings) -> PolicyEngine:
    policy_settings = settings.policy
    auto_actions = {
        getattr(ActionType, name.upper()) for name in policy_settings.level2_auto_actions
    }
    return PolicyEngine(
        max_actions=policy_settings.max_actions,
        max_files=policy_settings.max_files,
        max_modified_chars=policy_settings.max_modified_chars,
        max_level2_modified_chars=policy_settings.level2_max_modified_chars,
        level2_max_files=policy_settings.level2_max_files,
        level2_auto_actions=auto_actions,
        protected_prefixes=tuple(policy_settings.protected_path_prefixes),
    )


def _history_from_index(index: DerivedIndexService | None) -> HistoryService:
    db = getattr(index, "_db", None) if index is not None else None
    if db is not None:
        return HistoryService(HistoryRepository(db))
    return HistoryService()


def _agent_service_from(
    settings: Settings,
    vault: VaultService,
    index: DerivedIndexService | None,
    history: HistoryService,
) -> AgentJobService:
    """One bounded AgentJobService (shared by routes and the scheduler)."""
    ai_settings = settings.ai
    adapter = None
    if ai_settings.enabled and ai_settings.base_url:
        adapter = OpenAICompatibleAdapter(
            ai_settings.base_url,
            api_key=ai_settings.api_key,
            timeout_seconds=ai_settings.request_timeout_seconds,
            connect_timeout_seconds=ai_settings.connect_timeout_seconds,
            max_response_bytes=ai_settings.max_models_response_bytes,
            trust_env=bool(getattr(ai_settings, "use_env_proxy", False)),
        )
    tool_context = ToolContext(
        vault=vault,
        index=index,
        metadata=MetadataService(vault) if vault is not None else None,
        links=LinksService(index) if index is not None else None,
        search=SearchService(index) if index is not None else None,
    )
    return AgentJobService(
        vault,
        history=history,
        policy=_build_policy_engine(settings),
        adapter=adapter,
        registry=ToolRegistry(),
        planner=WorkflowPlanner(),
        tool_context=tool_context,
        settings=ai_settings,
        default_model=ai_settings.chat_model,
        protected_prefixes=tuple(settings.policy.protected_path_prefixes),
        max_journal_bytes=settings.history.max_journal_bytes,
    )


def get_agent_job_service(request: Request) -> AgentJobService:
    vault = get_vault_service(request)
    existing = getattr(request.app.state, "agent_job_service", None)
    if existing is not None:
        return existing
    settings = _settings_from_app(request)
    index = getattr(request.app.state, "index_service", None)
    history = _history_from_index(index)
    service = _agent_service_from(settings, vault, index, history)
    request.app.state.agent_job_service = service
    return service


def build_scheduler_service(
    settings: Settings,
    *,
    vault: VaultService | None = None,
    index: DerivedIndexService | None = None,
    agent_service: AgentJobService | None = None,
    history: HistoryService | None = None,
) -> SchedulerService:
    """Lifespan-time scheduler singleton (never built per request)."""
    from server.recovery.service import RecoveryService
    from server.scheduler.service import SchedulerService

    recovery: RecoveryService | None = None
    if history is not None and vault is not None:
        recovery = RecoveryService(vault, history)
    return SchedulerService(
        settings=settings.scheduler,
        server=settings.server,
        history=history,
        agent_service=agent_service,
        recovery=recovery,
        vault=vault,
        index_service=index,
        index_auto_rebuild=settings.index.auto_rebuild,
        history_settings=settings.history,
    )


def get_scheduler_service(request: Request) -> SchedulerService:
    from server.scheduler.errors import SchedulerUnavailable

    service = getattr(request.app.state, "scheduler_service", None)
    if service is None:
        raise SchedulerUnavailable("Scheduler is unavailable")
    return service
