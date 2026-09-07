"""M7 Schema tests (PLAN-M7 §9.1-2): extra fields, paths, semantics, caps."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from server.actions.schemas import (
    Action,
    ActionSet,
    ActionType,
    ActionValidationError,
    PatchHunk,
    validate_action,
    validate_action_set,
)
from server.actions.validators import parse_action_set
from server.vault.errors import VaultError

import m7support as m7


def _base_action(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "action": "add_tags",
        "file": "notes/today.md",
        "tags": ["work"],
        "reason": "tidy",
    }
    payload.update(overrides)
    return payload


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        Action.model_validate({**_base_action(), "delete_everything": True})
    with pytest.raises(ValidationError):
        ActionSet.model_validate(
            {"actions": [_base_action()], "task_type": "manual", "shell": "rm -rf /"}
        )


def test_natural_language_and_markdown_are_rejected() -> None:
    with pytest.raises(ActionValidationError):
        parse_action_set("please add the tag work to today's note")
    with pytest.raises(ActionValidationError):
        parse_action_set(["add work tag"])
    with pytest.raises(ActionValidationError):
        parse_action_set("add work to notes/today.md --tag work")


def test_parse_action_set_accepts_a_plain_json_object() -> None:
    payload = {"actions": [_base_action()], "task_type": "manual"}
    parsed = parse_action_set(payload)
    assert parsed.actions[0].file == "notes/today.md"
    assert parsed.task_type == "manual"
    assert str(parsed.actions[0].action_id)


@pytest.mark.parametrize(
    "bad_path",
    [
        "/abs/x.md",
        "../x.md",
        "a/../../x.md",
        "x\\y.md",
        "x\x00.md",
        "",
        "a/./b.md",
    ],
)
def test_illegal_paths_are_rejected(bad_path: str) -> None:
    with pytest.raises(VaultError):
        Action.model_validate(_base_action(file=bad_path))


def test_paths_with_backslash_or_nul_in_target_and_link_rejected() -> None:
    for field in ("target_file", "link_target"):
        with pytest.raises(VaultError):
            Action.model_validate(_base_action(**{field: "a\\b.md"}))
        with pytest.raises(VaultError):
            Action.model_validate(_base_action(**{field: "a\x00b.md"}))


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Action.model_validate(_base_action(action="delete_note"))


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "add_tags", "tags": []},
        {"action": "remove_tags", "tags": []},
        {"action": "add_link", "link_target": None},
        {"action": "move_note", "target_file": None},
        {"action": "patch_note", "patch": []},
        {"action": "patch_note", "patch": [{"start": 0}], "content_base64": "eA=="},
        {"action": "create_note", "content_base64": "eA==", "expected_sha256": "a" * 64},
        {"action": "create_note", "content_base64": None},
        {"action": "add_tags", "content_base64": "eA=="},
    ],
)
def test_field_mismatches_fail_semantic_validation(payload: dict[str, object]) -> None:
    action = Action.model_validate(_base_action(**payload))
    with pytest.raises(ActionValidationError):
        validate_action(action)


def test_duplicate_action_ids_rejected() -> None:
    shared = uuid4()
    actions = [
        Action.model_validate({**_base_action(file="a.md"), "action_id": shared}),
        Action.model_validate({**_base_action(file="b.md"), "action_id": shared}),
    ]
    with pytest.raises(ActionValidationError):
        validate_action_set(ActionSet(actions=actions, task_type="manual"))


def test_reason_too_long_and_tag_caps() -> None:
    with pytest.raises(ValidationError):
        Action.model_validate(_base_action(reason="x" * 501))
    with pytest.raises(ValidationError):
        Action.model_validate(_base_action(tags=["t%d" % index for index in range(11)]))
    with pytest.raises(ValidationError):
        Action.model_validate(_base_action(tags=["bad\ntag"]))


def test_action_set_caps_and_emptiness() -> None:
    with pytest.raises(ActionValidationError):
        validate_action_set(ActionSet(actions=[], task_type="manual"))
    many = [
        Action.model_validate(_base_action(file="f%d.md" % index))
        for index in range(20)
    ]
    with pytest.raises(ValidationError):
        ActionSet(
            actions=[
                Action.model_validate(_base_action(file="f%d.md" % index))
                for index in range(21)
            ],
            task_type="manual",
        )
    assert validate_action_set(ActionSet(actions=many, task_type="manual")).actions


def test_create_content_size_and_sha_bounds() -> None:
    with pytest.raises(ValidationError):
        Action.model_validate(
            _base_action(action="create_note", content_base64=m7.b64(b"x" * 1_000_001))
        )
    with pytest.raises(ValidationError):
        Action.model_validate(
            _base_action(action="patch_note", expected_sha256="not-a-hash")
        )
    with pytest.raises(ValidationError):
        Action.model_validate(
            _base_action(
                action="patch_note",
                patch=[PatchHunk(start=0, old_text="", new_text="x" * 10_001)],
            )
        )


def test_model_owned_metadata_parses_but_is_untrusted() -> None:
    payload = {
        "actions": [
            _base_action(
                action="create_note",
                file="notes/new.md",
                content_base64=m7.b64("# New\n"),
                permission_level=2,
            )
        ],
        "task_type": "weekly_review",
        "model": "attacker-model",
        "prompt_version": "forged",
    }
    parsed = parse_action_set(payload)
    assert parsed.model == "attacker-model"
    assert parsed.actions[0].permission_level == 2


def test_json_schema_contains_strict_action_properties() -> None:
    schema = ActionSet.model_json_schema()
    dumped = json.dumps(schema)
    assert "actions" in schema["properties"]
    assert "task_type" in schema["properties"]
    assert schema.get("additionalProperties") is False
    for field in ("action", "file", "reason", "permission_level", "action_id", "tags"):
        assert json.dumps(field) in dumped


def test_all_whitelisted_wire_actions_parse() -> None:
    for kind in ActionType:
        base = dict(_base_action(action=kind.value))
        if kind == ActionType.CREATE_NOTE:
            base["content_base64"] = m7.b64("# new\n")
        elif kind == ActionType.ADD_LINK:
            base["link_target"] = "notes/other.md"
        elif kind == ActionType.MOVE_NOTE:
            base["target_file"] = "notes/moved.md"
            base["expected_sha256"] = "a" * 64
        elif kind == ActionType.PATCH_NOTE:
            base["patch"] = [{"start": 0, "old_text": "x", "new_text": "y"}]
            base["expected_sha256"] = "b" * 64
        action = Action.model_validate(base)
        validate_action(action)
        assert action.action == kind
