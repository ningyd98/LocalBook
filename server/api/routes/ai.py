"""GET /api/v1/ai/status — read-only oMLX discovery probe.

Degraded AI states (``not_configured`` / ``offline``) are returned as HTTP 200
with the explicit AIStatusResponse schema (see server/ai/schemas.py), so the
frontend never mistakes "AI offline" for an API crash. Only unrecoverable
internal failures produce 5xx.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ...ai.schemas import (
    AIStatusResponse,
    ChatRequest,
    ChatResponse,
    ClassifyRequest,
    ClassifyResponse,
    ExtractTodosRequest,
    ExtractTodosResponse,
    RelatedRequest,
    RelatedResponse,
    SummarizeRequest,
    SummarizeResponse,
    TagsRequest,
    TagsResponse,
)
from ...ai.service import AIStatusService
from ...ai.workflows import AIWorkflowService
from ..dependencies import get_ai_status_service, get_ai_workflow_service

router = APIRouter(prefix="/api/v1", tags=["ai"])

AIServiceDep = Annotated[AIStatusService, Depends(get_ai_status_service)]
AIWorkflowDep = Annotated[AIWorkflowService, Depends(get_ai_workflow_service)]


@router.get("/ai/status", response_model=AIStatusResponse)
async def ai_status(service: AIServiceDep) -> AIStatusResponse:
    return await service.check()


@router.post("/ai/chat", response_model=ChatResponse)
async def ai_chat(request: ChatRequest, service: AIWorkflowDep) -> ChatResponse:
    return await service.chat(request)


@router.post("/ai/summarize", response_model=SummarizeResponse)
async def ai_summarize(request: SummarizeRequest, service: AIWorkflowDep):
    return await service.summarize(request)


@router.post("/ai/tags", response_model=TagsResponse)
async def ai_tags(request: TagsRequest, service: AIWorkflowDep):
    return await service.tags(request)


@router.post("/ai/related", response_model=RelatedResponse)
async def ai_related(request: RelatedRequest, service: AIWorkflowDep):
    return await service.related(request)


@router.post("/ai/extract_todos", response_model=ExtractTodosResponse)
async def ai_extract_todos(request: ExtractTodosRequest, service: AIWorkflowDep):
    return await service.extract_todos(request)


@router.post("/ai/classify", response_model=ClassifyResponse)
async def ai_classify(request: ClassifyRequest, service: AIWorkflowDep):
    return await service.classify(request)
