"""Reader knowledge questions must retrieve note evidence before answering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.ai.adapters.base import ChatResult
from server.ai.schemas import AICapabilities, DiscoveredModel
from server.ai.workflows import AIWorkflowService
from server.api.main import create_app
from server.config import AISettings, SchedulerSettings, Settings, VaultSettings
from server.reader.database import ReaderDatabase
from server.reader.schemas import AskRequest, DeviceType
from server.reader.service import ReaderService, generate_uuidv7
from tests.backend.client import TestClient


class _CitationAdapter:
    async def list_models(self) -> list[DiscoveredModel]:
        return [DiscoveredModel(id="test-model", capabilities=AICapabilities(chat=True))]

    async def chat(self, **kwargs) -> ChatResult:
        return ChatResult(
            content=json.dumps(
                {
                    "answer": "Orion 使用 SQLite。",
                    "citations": [
                        {
                            "path": "orion.md",
                            "quote": "项目数据库使用 SQLite。",
                        }
                    ],
                }
            ),
            model="test-model",
        )


def test_my_knowledge_question_retrieves_and_cites_matching_note(tmp_path: Path) -> None:
    """Natural question wording must not turn a known match into zero context."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "orion.md").write_text(
        "# Orion 项目\n\n项目数据库使用 SQLite。\n", encoding="utf-8"
    )
    settings = Settings(
        vault=VaultSettings(root=root, watcher_enabled=False),
        scheduler=SchedulerSettings(enabled=False),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        pair = client.post(
            "/api/v1/reader/pair",
            json={"device_name": "Temporary test", "device_type": "macos"},
        )
        code = pair.json()["pairing_token"]
        approved = client.post(
            "/api/v1/settings/reader/pairing/approve", json={"pairing_token": code}
        )
        assert approved.status_code == 200
        registered = client.post(
            "/api/v1/reader/register",
            json={
                "device_id": generate_uuidv7(),
                "device_name": "Temporary test",
                "device_type": "macos",
                "pairing_token": code,
            },
        )
        assert registered.status_code == 200
        workflow = AIWorkflowService(
            AISettings(chat_model="test-model"),
            _CitationAdapter(),
            vault=app.state.vault_service,
        )
        app.state.reader_service.ai_workflow = workflow
        response = client.post(
            "/api/v1/reader/ask",
            json={"query": "Orion 项目使用什么数据库？", "scope": "my_knowledge"},
            headers={"Authorization": f"Bearer {registered.json()['access_token']}"},
        )

    assert response.status_code == 200
    assert [item["source_id"] for item in response.json()["citations"]] == ["orion.md"]


@pytest.mark.asyncio
async def test_my_knowledge_without_matches_does_not_generate_a_factual_answer(
    tmp_path: Path,
) -> None:
    """A search miss must be explicit instead of becoming an uncited model answer."""
    database = ReaderDatabase(str(tmp_path / "reader.db"))
    code, _ = database.create_pairing_token("Temporary test", DeviceType.MACOS)
    database.approve_pairing_token(code)
    device_id = generate_uuidv7()
    database.verify_and_register_device(device_id, "Temporary test", DeviceType.MACOS, code)
    workflow = AIWorkflowService(AISettings(chat_model="test-model"), _CitationAdapter())
    reader = ReaderService(database, ai_workflow=workflow)

    result = await reader.ask(
        AskRequest(device_id=device_id, query="未知项目采用什么数据库？", scope="my_knowledge")
    )

    assert result.model is None
    assert result.citations == []
    assert "没有找到" in result.answer
