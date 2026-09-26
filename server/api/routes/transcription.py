"""REST endpoint for local audio transcription."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ...transcription.schemas import TranscriptionRequest, TranscriptionResponse
from ...transcription.service import LocalTranscriptionService
from ..dependencies import get_transcription_service

router = APIRouter(prefix="/api/v1/transcription", tags=["transcription"])
TranscriptionServiceDep = Annotated[
    LocalTranscriptionService,
    Depends(get_transcription_service),
]


@router.post("", response_model=TranscriptionResponse)
def transcribe_audio(
    request: TranscriptionRequest,
    service: TranscriptionServiceDep,
) -> TranscriptionResponse:
    return TranscriptionResponse(
        **service.transcribe(request.path, language=request.language)
    )


__all__ = ["router"]
