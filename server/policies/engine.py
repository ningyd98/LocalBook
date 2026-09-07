"""Pure deterministic policy engine for M7 (PLAN-M7 §5.2).

The engine never imports an AI adapter/workflow, never touches the
filesystem and never reads History.  It receives pre-computed
:class:`ActionFacts` and answers ``allow | deny | confirm`` with the
``matched_rules`` evidence for each decision.  Decision order is fixed:
deny takes precedence, then confirm (Level 1), then allow (Level 2 auto set
only, closed by default).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from server.actions.schemas import Action, ActionType, PermissionLevel
from server.policies.rules import (
    AUTO_ACTIONS,
    DEFAULT_PROTECTED_PREFIXES,
    LEVEL2_MAX_FILES,
    MAX_ACTIONS,
    MAX_FILES,
    MAX_LEVEL1_MODIFIED_CHARS,
    MAX_LEVEL2_MODIFIED_CHARS,
    MIN_LEVEL_BY_ACTION,
)


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"


@dataclass
class ActionFacts:
    """Read-only facts gathered by the service before policy evaluation."""

    existing: bool = True
    protected: bool = False
    overwrite: bool = False
    file_count: int = 1
    modified_chars: int = 0
    scope: str = "single-file"
    target_exists: bool = False
    attachment: bool = False
    markdown: bool = True
    recursive: bool = False
    allowlisted_target: bool = True
    body_change: bool = False
    duplicate_tags: bool = False
    tags_present: bool = True
    link_target_exists: bool = True


class PolicyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: PolicyDecision
    level: int
    action_type: str
    matched_rules: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    budgets: dict[str, int] = Field(default_factory=dict)


class PolicySetResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: PolicyDecision
    action_results: list[PolicyResult] = Field(default_factory=list)
    matched_rules: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


def _deny(result: PolicyResult, rule: str, reason: str) -> PolicyResult:
    if rule not in result.matched_rules:
        result.matched_rules.append(rule)
        result.reasons.append(reason)
    result.decision = PolicyDecision.DENY
    return result


class PolicyEngine:
    """Deterministic, AI-free rule evaluator.

    ``level2_auto_actions`` is empty by default: Level 2 automatic execution
    is closed until a deployment explicitly opts into tag-only actions.
    """

    def __init__(
        self,
        *,
        max_actions: int = MAX_ACTIONS,
        max_files: int = MAX_FILES,
        max_modified_chars: int = MAX_LEVEL1_MODIFIED_CHARS,
        max_level2_modified_chars: int = MAX_LEVEL2_MODIFIED_CHARS,
        level2_max_files: int = LEVEL2_MAX_FILES,
        level2_auto_actions: frozenset[ActionType]
        | set[ActionType]
        | list[ActionType] = frozenset(),
        protected_prefixes: tuple[str, ...] = DEFAULT_PROTECTED_PREFIXES,
    ) -> None:
        self.max_actions = max(1, min(int(max_actions), MAX_ACTIONS))
        self.max_files = max(1, min(int(max_files), MAX_FILES))
        self.max_modified_chars = max(0, min(int(max_modified_chars), MAX_LEVEL1_MODIFIED_CHARS))
        self.max_level2_modified_chars = max(
            0, min(int(max_level2_modified_chars), MAX_LEVEL2_MODIFIED_CHARS)
        )
        self.level2_max_files = max(1, min(int(level2_max_files), self.max_files))
        # The configured auto set can never exceed the hard AUTO_ACTIONS set.
        requested = set(level2_auto_actions)
        self.level2_auto_actions = frozenset(requested) & AUTO_ACTIONS
        self.protected_prefixes = tuple(protected_prefixes) or DEFAULT_PROTECTED_PREFIXES

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        action: Action,
        *,
        facts: ActionFacts | None = None,
        level: int | None = None,
    ) -> PolicyResult:
        """Evaluate one action against one fact snapshot (pure function)."""
        facts = facts or ActionFacts()
        effective_level = int(action.permission_level) if level is None else int(level)
        result = PolicyResult(
            decision=PolicyDecision.CONFIRM,
            level=effective_level,
            action_type=action.action.value,
            matched_rules=[],
            reasons=[],
            budgets={
                "max_actions": self.max_actions,
                "max_files": self.max_files,
                "max_modified_chars": self.max_modified_chars,
                "max_level2_modified_chars": self.max_level2_modified_chars,
                "level2_max_files": self.level2_max_files,
            },
        )
        self._evaluate_deny_rules(action, facts, effective_level, result)
        if result.decision == PolicyDecision.DENY:
            return result
        if effective_level == PermissionLevel.READ_ONLY:
            return _deny(result, "readonly_mutation", "Read-only level cannot mutate")
        if effective_level == PermissionLevel.SUGGEST:
            result.matched_rules.append("level1_requires_confirmation")
            result.reasons.append("Explicit confirmation required")
            return result
        # Level 2 (LOW_RISK_AUTO): allow only the configured tag-only subset.
        self._evaluate_level2(action, facts, result)
        return result

    def _evaluate_deny_rules(
        self,
        action: Action,
        facts: ActionFacts,
        level: int,
        result: PolicyResult,
    ) -> None:
        """Append every matched deny rule; any match leaves decision DENY."""
        if action.action not in AUTO_ACTIONS and action.action not in MIN_LEVEL_BY_ACTION:
            _deny(result, "action_not_allowed", f"{action.action.value} is not a wire action")
            return
        if level not in (0, 1, 2):
            _deny(result, "invalid_level", "Permission level must be 0, 1 or 2")
        if level < MIN_LEVEL_BY_ACTION.get(action.action, 1):
            _deny(result, "level_too_low", "Action requires a higher permission level")
        if facts.protected or self._is_protected(action.file) or (
            action.target_file and self._is_protected(action.target_file)
        ):
            _deny(result, "protected_path", "Path is protected")
        if facts.recursive:
            _deny(result, "recursive_operation", "Recursive operations are denied")
        if facts.scope != "single-file":
            _deny(result, "multi_file_scope", "Only single-file operations are allowed")
        if facts.file_count > self.max_files:
            _deny(result, "file_count_budget", "Too many files for one action")
        if facts.attachment:
            _deny(result, "attachment_write", "Attachment writes are permanently denied")
        if not facts.markdown:
            _deny(result, "markdown_required", "Only Markdown notes can be mutated")
        if not facts.allowlisted_target:
            _deny(result, "target_not_allowlisted", "Target is outside the allow-list")
        if facts.overwrite:
            _deny(result, "overwrite_denied", "Overwriting files is denied")
        # Action semantics against real file state.
        mutating_existing = action.action in (
            ActionType.ADD_TAGS,
            ActionType.REMOVE_TAGS,
            ActionType.ADD_LINK,
            ActionType.PATCH_NOTE,
            ActionType.MOVE_NOTE,
        )
        if mutating_existing and not facts.existing:
            _deny(result, "missing_file", f"{action.file} does not exist")
        if action.action == ActionType.CREATE_NOTE and facts.existing:
            _deny(result, "create_existing", "create_note cannot overwrite an existing file")
        if action.action == ActionType.MOVE_NOTE:
            if not action.target_file:
                _deny(result, "missing_target", "move_note requires a target_file")
            if facts.target_exists:
                _deny(result, "move_overwrite", "move_note cannot overwrite its target")
        if action.action in (ActionType.PATCH_NOTE, ActionType.MOVE_NOTE):
            if not action.expected_sha256:
                _deny(result, "expected_hash_required", "expected_sha256 is required")
        if action.action == ActionType.ADD_LINK and not facts.link_target_exists:
            _deny(result, "broken_link_target", "Link target is missing or not allow-listed")
        if action.action == ActionType.REMOVE_TAGS and not facts.tags_present:
            _deny(result, "unknown_tag_remove", "Requested tags are not present in the note")
        if action.action == ActionType.ADD_TAGS and facts.duplicate_tags:
            _deny(result, "duplicate_tag_add", "One or more tags already exist on the note")
        budget = (
            self.max_level2_modified_chars
            if level == PermissionLevel.LOW_RISK_AUTO
            else self.max_modified_chars
        )
        if facts.modified_chars > budget:
            _deny(result, "character_budget", "Modification budget exceeded")
        if level == PermissionLevel.LOW_RISK_AUTO and facts.body_change:
            _deny(result, "level2_body_change", "Level 2 actions must not change the body")

    def _evaluate_level2(self, action: Action, facts: ActionFacts, result: PolicyResult) -> None:
        if action.action not in self.level2_auto_actions:
            _deny(
                result,
                "level2_disallowed_action",
                "Action is outside the configured Level 2 auto set",
            )
            return
        if not facts.existing or facts.overwrite or facts.protected:
            return  # already denied above
        if facts.file_count > self.level2_max_files or facts.scope != "single-file":
            _deny(result, "level2_file_budget", "Level 2 auto is limited to one file")
            return
        if facts.body_change or facts.modified_chars > self.max_level2_modified_chars:
            return  # already denied above
        result.decision = PolicyDecision.ALLOW
        result.matched_rules = ["level2_allow"]
        result.reasons = ["Low-risk automatic action"]

    def _is_protected(self, relative_path: str) -> bool:
        lowered = relative_path.casefold()
        return any(
            lowered == prefix or lowered.startswith(prefix + "/")
            for prefix in self.protected_prefixes
        )

    @staticmethod
    def _mutated_paths(action: Action) -> set[str]:
        """The paths one action mutates (source + no-overwrite target)."""
        paths = {action.file}
        if action.action == ActionType.MOVE_NOTE and action.target_file:
            paths.add(action.target_file)
        return paths

    def _conflicting_paths(self, actions: list[Action]) -> list[str]:
        """Return paths mutated by more than one action (deny whole set)."""
        seen: dict[str, int] = defaultdict(int)
        for action in actions:
            for path in self._mutated_paths(action):
                seen[path] += 1
        return sorted(path for path, count in seen.items() if count > 1)

    # ------------------------------------------------------------------
    # Set evaluation
    # ------------------------------------------------------------------

    def evaluate_set(
        self,
        actions: list[Action],
        *,
        facts: ActionFacts | list[ActionFacts] | None = None,
        level: int | None = None,
    ) -> PolicySetResult:
        """Evaluate a whole set with deny precedence and aggregate budgets.

        ``facts`` may be one snapshot applied to every action or one per
        action (matching index order).  A single deny anywhere denies the
        whole set — no partial execution is ever proposed.
        """
        empty = PolicySetResult(decision=PolicyDecision.DENY, action_results=[])
        if not actions:
            empty.matched_rules = ["action_budget"]
            empty.reasons = ["Action set must not be empty"]
            return empty
        if len(actions) > self.max_actions:
            empty.matched_rules = ["action_budget"]
            empty.reasons = ["Action budget exceeded"]
            return empty
        fact_list: list[ActionFacts]
        if facts is None:
            fact_list = [ActionFacts() for _ in actions]
        elif isinstance(facts, ActionFacts):
            fact_list = [facts for _ in actions]
        else:
            fact_list = list(facts)
            if len(fact_list) != len(actions):
                fact_list = (fact_list + [ActionFacts()] * len(actions))[: len(actions)]
        conflict = self._conflicting_paths(actions)
        if conflict:
            denied = PolicyResult(
                decision=PolicyDecision.DENY,
                level=level or 1,
                action_type="set",
                matched_rules=["conflicting_actions"],
                reasons=[f"multiple actions mutate the same path: {conflict}"],
                budgets={},
            )
            return PolicySetResult(
                decision=PolicyDecision.DENY,
                action_results=[denied],
                matched_rules=["conflicting_actions"],
                reasons=[f"multiple actions mutate the same path: {conflict}"],
            )
        results = [
            self.evaluate(action, facts=fact_list[index], level=level)
            for index, action in enumerate(actions)
        ]
        total_chars = sum(result_facts.modified_chars for result_facts in fact_list)
        if total_chars > self.max_modified_chars:
            denied = PolicyResult(
                decision=PolicyDecision.DENY,
                level=level or 1,
                action_type="set",
                matched_rules=["character_budget"],
                reasons=["Aggregate modification budget exceeded"],
                budgets={},
            )
            return PolicySetResult(
                decision=PolicyDecision.DENY,
                action_results=[*results, denied],
                matched_rules=["character_budget"],
                reasons=["Aggregate modification budget exceeded"],
            )
        matched: list[str] = []
        reasons: list[str] = []
        if any(item.decision == PolicyDecision.DENY for item in results):
            decision = PolicyDecision.DENY
            matched.append("deny_precedence")
            reasons.append("One or more actions were denied")
        elif any(item.decision == PolicyDecision.CONFIRM for item in results):
            decision = PolicyDecision.CONFIRM
            matched.append("level1_requires_confirmation")
            reasons.append("Explicit confirmation required")
        else:
            decision = PolicyDecision.ALLOW
            matched.append("all_actions_allowed")
            reasons.append("Every action is low-risk and allowed")
        for item in results:
            matched.extend(rule for rule in item.matched_rules if rule not in matched)
            reasons.extend(reason for reason in item.reasons if reason not in reasons)
        return PolicySetResult(
            decision=decision,
            action_results=results,
            matched_rules=matched,
            reasons=reasons,
        )


def decision_to_value(decision: PolicyDecision) -> str:
    return decision.value


__all__ = [
    "ActionFacts",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyResult",
    "PolicySetResult",
    "decision_to_value",
]
