"""Semantic validation boundary before policy evaluation (M7)."""

from __future__ import annotations

from pydantic import ValidationError

from .schemas import ActionSet, ActionValidationError, validate_action_set

__all__ = ["ActionValidationError", "parse_action_set"]


def parse_action_set(payload: object) -> ActionSet:
    """Parse untrusted model output into a strictly validated ActionSet.

    ``payload`` must already be a JSON-decoded object.  Anything else
    (natural-language text, lists, scalar values) raises a stable
    :class:`ActionValidationError` instead of being coerced.
    """
    if not isinstance(payload, dict):
        raise ActionValidationError(
            "action output must be a JSON object with an actions array"
        )
    try:
        return validate_action_set(ActionSet.model_validate(payload))
    except ActionValidationError:
        raise
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.errors()[0].get("loc", ()))
        message = exc.errors()[0].get("msg", "invalid action")
        raise ActionValidationError(f"invalid action field {location}: {message}") from exc
