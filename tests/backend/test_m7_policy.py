"""M7 Policy engine matrix tests (PLAN-M7 §9.1-1).

Policy is pure: no AI, no filesystem, no HTTP imports.  A single deny denies
the whole set; Level 1 mutations confirm; Level 2 is closed by default and
only opens for configured tag-only single-file changes.
"""

from __future__ import annotations

import pytest

from server.actions.schemas import Action, ActionType
from server.policies.engine import (
    ActionFacts,
    PolicyDecision,
    PolicyEngine,
)
from server.policies.rules import AUTO_ACTIONS


def _action(kind: ActionType, **extra: object) -> Action:
    payload: dict[str, object] = {
        "action": kind.value,
        "file": "notes/a.md",
        "tags": ["x"],
        "reason": "policy test",
    }
    payload.update(extra)
    if kind == ActionType.PATCH_NOTE:
        payload["patch"] = [{"start": 0, "old_text": "a", "new_text": "b"}]
        payload["expected_sha256"] = "a" * 64
    if kind == ActionType.ADD_LINK:
        payload["link_target"] = "notes/b.md"
    if kind == ActionType.MOVE_NOTE:
        payload["target_file"] = "notes/c.md"
        payload["expected_sha256"] = "a" * 64
    if kind == ActionType.CREATE_NOTE:
        payload["content_base64"] = "IyBuZXcK"
    return Action.model_validate(payload)


def test_engine_has_no_ai_or_fs_imports() -> None:
    source = open("server/policies/engine.py", encoding="utf-8").read()
    lowered = source.casefold()
    assert "import" in lowered
    for banned in ("ai.adapters", "ai.workflows", "import httpx", "open(", "vault.service"):
        assert banned not in lowered


@pytest.mark.parametrize("kind", list(ActionType))
def test_level0_mutation_is_denied(kind: ActionType) -> None:
    engine = PolicyEngine()
    result = engine.evaluate(_action(kind), facts=ActionFacts(existing=True), level=0)
    assert result.decision == PolicyDecision.DENY
    assert "readonly_mutation" in result.matched_rules or "level_too_low" in result.matched_rules


@pytest.mark.parametrize("kind", list(ActionType))
def test_level1_legal_mutation_confirms(kind: ActionType) -> None:
    engine = PolicyEngine()
    existing = kind != ActionType.CREATE_NOTE
    facts = ActionFacts(existing=existing, target_exists=False, link_target_exists=True)
    result = engine.evaluate(_action(kind), facts=facts, level=1)
    assert result.decision == PolicyDecision.CONFIRM
    assert "level1_requires_confirmation" in result.matched_rules


def test_level2_is_closed_by_default() -> None:
    engine = PolicyEngine()  # level2_auto_actions defaults to empty
    for kind in AUTO_ACTIONS:
        facts = ActionFacts(existing=True, body_change=False, modified_chars=20)
        result = engine.evaluate(_action(kind), facts=facts, level=2)
        assert result.decision == PolicyDecision.DENY
        assert "level2_disallowed_action" in result.matched_rules


def test_level2_tag_only_single_file_can_be_opened() -> None:
    engine = PolicyEngine(level2_auto_actions={ActionType.ADD_TAGS}, level2_max_files=1)
    facts = ActionFacts(existing=True, modified_chars=20, body_change=False)
    result = engine.evaluate(_action(ActionType.ADD_TAGS), facts=facts, level=2)
    assert result.decision == PolicyDecision.ALLOW
    assert "level2_allow" in result.matched_rules
    # body change and multi-file are denied even when the tag is configured
    result = engine.evaluate(
        _action(ActionType.ADD_TAGS),
        facts=ActionFacts(existing=True, modified_chars=20, body_change=True),
        level=2,
    )
    assert result.decision == PolicyDecision.DENY
    assert "level2_body_change" in result.matched_rules


def test_level2_non_auto_actions_are_denied() -> None:
    engine = PolicyEngine(level2_auto_actions={ActionType.ADD_TAGS})
    for kind in (ActionType.PATCH_NOTE, ActionType.MOVE_NOTE, ActionType.CREATE_NOTE, ActionType.ADD_LINK):
        facts = ActionFacts(
            existing=kind != ActionType.CREATE_NOTE,
            target_exists=False,
            modified_chars=0,
            body_change=False,
        )
        result = engine.evaluate(_action(kind), facts=facts, level=2)
        assert result.decision == PolicyDecision.DENY
        assert "level2_disallowed_action" in result.matched_rules


@pytest.mark.parametrize(
    ("facts", "rule"),
    [
        (ActionFacts(protected=True), "protected_path"),
        (ActionFacts(existing=True, recursive=True), "recursive_operation"),
        (ActionFacts(existing=True, scope="batch"), "multi_file_scope"),
        (ActionFacts(existing=True, attachment=True), "attachment_write"),
        (ActionFacts(existing=True, markdown=False), "markdown_required"),
        (ActionFacts(existing=True, allowlisted_target=False), "target_not_allowlisted"),
        (ActionFacts(existing=True, overwrite=True), "overwrite_denied"),
        (ActionFacts(existing=False), "missing_file"),
        (ActionFacts(existing=True, modified_chars=25_000), "character_budget"),
        (ActionFacts(existing=True, file_count=11), "file_count_budget"),
    ],
)
def test_deny_rules_fire_for_add_tags(facts: ActionFacts, rule: str) -> None:
    engine = PolicyEngine()
    result = engine.evaluate(_action(ActionType.ADD_TAGS), facts=facts, level=1)
    assert result.decision == PolicyDecision.DENY
    assert rule in result.matched_rules


def test_action_specific_semantics() -> None:
    engine = PolicyEngine()
    # create cannot overwrite an existing file
    r = engine.evaluate(
        _action(ActionType.CREATE_NOTE), facts=ActionFacts(existing=True), level=1
    )
    assert r.decision == PolicyDecision.DENY and "create_existing" in r.matched_rules
    # move cannot overwrite its target and needs an existing source
    r = engine.evaluate(
        _action(ActionType.MOVE_NOTE),
        facts=ActionFacts(existing=True, target_exists=True),
        level=1,
    )
    assert r.decision == PolicyDecision.DENY and "move_overwrite" in r.matched_rules
    # patch without expected hash is denied
    patch = _action(ActionType.PATCH_NOTE)
    patch = patch.model_copy(update={"expected_sha256": None})
    r = engine.evaluate(patch, facts=ActionFacts(existing=True), level=1)
    assert r.decision == PolicyDecision.DENY and "expected_hash_required" in r.matched_rules
    # add_link with a missing/broken target is denied
    r = engine.evaluate(
        _action(ActionType.ADD_LINK),
        facts=ActionFacts(existing=True, link_target_exists=False),
        level=1,
    )
    assert r.decision == PolicyDecision.DENY and "broken_link_target" in r.matched_rules
    # remove_tags for absent tags denied
    r = engine.evaluate(
        _action(ActionType.REMOVE_TAGS),
        facts=ActionFacts(existing=True, tags_present=False),
        level=1,
    )
    assert r.decision == PolicyDecision.DENY and "unknown_tag_remove" in r.matched_rules
    # add_tags that duplicate existing tags denied
    r = engine.evaluate(
        _action(ActionType.ADD_TAGS),
        facts=ActionFacts(existing=True, duplicate_tags=True),
        level=1,
    )
    assert r.decision == PolicyDecision.DENY and "duplicate_tag_add" in r.matched_rules


def test_invalid_level_is_denied() -> None:
    engine = PolicyEngine()
    result = engine.evaluate(_action(ActionType.ADD_TAGS), facts=ActionFacts(), level=7)
    assert result.decision == PolicyDecision.DENY
    assert "invalid_level" in result.matched_rules


def test_set_level_empty_and_over_budget() -> None:
    engine = PolicyEngine(max_actions=3)
    denied = engine.evaluate_set([], level=1)
    assert denied.decision == PolicyDecision.DENY and "action_budget" in denied.matched_rules
    too_many = engine.evaluate_set(
        [_action(ActionType.ADD_TAGS, file="n%d.md" % i) for i in range(4)], level=1
    )
    assert too_many.decision == PolicyDecision.DENY


def test_set_deny_precedence_over_confirm_and_allow() -> None:
    engine = PolicyEngine(level2_auto_actions={ActionType.ADD_TAGS})
    good = engine.evaluate(
        _action(ActionType.ADD_TAGS),
        facts=ActionFacts(existing=True, body_change=False, modified_chars=10),
        level=2,
    )
    assert good.decision == PolicyDecision.ALLOW
    bad = engine.evaluate(
        _action(ActionType.ADD_TAGS),
        facts=ActionFacts(existing=True, protected=True),
        level=2,
    )
    assert bad.decision == PolicyDecision.DENY
    results = engine.evaluate_set(
        [_action(ActionType.ADD_TAGS, file="a.md"), _action(ActionType.ADD_TAGS, file="b.md")],
        facts=[ActionFacts(existing=True, body_change=False, modified_chars=10), ActionFacts(existing=True, protected=True)],
        level=2,
    )
    assert results.decision == PolicyDecision.DENY
    assert "deny_precedence" in results.matched_rules


def test_conflicting_actions_on_same_path_deny_the_set() -> None:
    engine = PolicyEngine()
    results = engine.evaluate_set(
        [
            _action(ActionType.ADD_TAGS, file="notes/a.md", tags=["x"]),
            _action(ActionType.REMOVE_TAGS, file="notes/a.md", tags=["y"]),
        ],
        facts=[ActionFacts(existing=True), ActionFacts(existing=True)],
        level=1,
    )
    assert results.decision == PolicyDecision.DENY
    assert "conflicting_actions" in results.matched_rules


def test_aggregate_character_budget() -> None:
    engine = PolicyEngine(max_modified_chars=100)
    results = engine.evaluate_set(
        [
            _action(ActionType.ADD_TAGS, file="a.md"),
            _action(ActionType.ADD_TAGS, file="b.md"),
        ],
        facts=[
            ActionFacts(existing=True, modified_chars=60, body_change=False),
            ActionFacts(existing=True, modified_chars=60, body_change=False),
        ],
        level=1,
    )
    assert results.decision == PolicyDecision.DENY
    assert "character_budget" in results.matched_rules


def test_level2_open_is_reachable_through_settings_and_dependency_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重要-2 regression: Settings → _build_policy_engine must keep Level 2
    usable.  A non-empty ``level2_auto_actions`` with a zero-char budget is a
    configuration error (it would deny every tag action via character_budget);
    an explicit positive budget opens tag-only auto for real."""
    from pydantic import ValidationError

    from server.api import dependencies as deps
    from server.config import PolicySettings, Settings

    for key in (
        "LOCALNOTE_POLICY__LEVEL2_AUTO_ACTIONS",
        "LOCALNOTE_POLICY__LEVEL2_MAX_MODIFIED_CHARS",
        "LOCALNOTE_POLICY__LEVEL2_MAX_FILES",
    ):
        monkeypatch.delenv(key, raising=False)
    # Defaults keep Level 2 closed (empty auto set) — no error, budget 0.
    closed = deps._build_policy_engine(Settings())
    closed_result = closed.evaluate(
        _action(ActionType.ADD_TAGS),
        facts=ActionFacts(existing=True, modified_chars=12, body_change=False),
        level=2,
    )
    assert closed_result.decision == PolicyDecision.DENY
    # Opting into tag auto without a budget is a config error, not a silent
    # all-denying Level 2.
    with pytest.raises(ValidationError):
        PolicySettings(level2_auto_actions=["add_tags"])
    with pytest.raises(ValidationError):
        Settings(policy={"level2_auto_actions": ["remove_tags"]})
    # Explicit opt-in (auto tags + budget) opens Level 2 end to end.
    settings = Settings(
        policy={
            "level2_auto_actions": ["add_tags"],
            "level2_max_modified_chars": 2000,
        }
    )
    engine = deps._build_policy_engine(settings)
    assert engine.level2_auto_actions == {ActionType.ADD_TAGS}
    assert engine.max_level2_modified_chars == 2000
    result = engine.evaluate(
        _action(ActionType.ADD_TAGS, tags=["x"]),
        facts=ActionFacts(existing=True, modified_chars=12, body_change=False),
        level=2,
    )
    assert result.decision == PolicyDecision.ALLOW
    assert "level2_allow" in result.matched_rules
