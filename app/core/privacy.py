"""Preferência de privacidade visual da interface."""

from __future__ import annotations

import re

from flask import session

VALUES_HIDDEN_SESSION_KEY = "values_hidden"
VALUES_MASK = "****"
_MONEY_TEXT_RE = re.compile(
    r"(?P<prefix>[+-]?\s*)(?:R\$|US\$)\s*"
    r"(?:\d{1,3}(?:\.\d{3})+|\d+),(?:\d{2})"
)


def values_hidden() -> bool:
    return bool(session.get(VALUES_HIDDEN_SESSION_KEY, False))


def mask_value(value: str) -> str:
    return VALUES_MASK if values_hidden() else value


def mask_text(value: str) -> str:
    if not values_hidden():
        return value
    return _MONEY_TEXT_RE.sub(
        lambda match: f"{match.group('prefix')}{VALUES_MASK}", value
    )

