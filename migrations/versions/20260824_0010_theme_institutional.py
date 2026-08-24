"""Reintroduz o tema institutional original na constraint de tema.

Revision ID: 20260824_0010_theme2
Revises: 20260822_0009_theme
"""

from __future__ import annotations

from alembic import op

revision = "20260824_0010_theme2"
down_revision = "20260822_0009_theme"
branch_labels = None
depends_on = None

_OLD_THEMES = (
    "light", "dark", "solarized_light", "solarized_dark", "dracula", "nord",
    "monokai", "gray", "soft_light", "soft_dark", "corporate_blue", "emerald",
)
_NEW_THEMES = ("institutional",) + _OLD_THEMES


def upgrade() -> None:
    op.drop_constraint("theme_valid", "app_settings", type_="check")
    values = ", ".join(f"'{theme}'" for theme in _NEW_THEMES)
    op.create_check_constraint(
        "theme_valid",
        "app_settings",
        f"theme IN ({values})",
    )


def downgrade() -> None:
    op.drop_constraint("theme_valid", "app_settings", type_="check")
    values = ", ".join(f"'{theme}'" for theme in _OLD_THEMES)
    op.create_check_constraint(
        "theme_valid",
        "app_settings",
        f"theme IN ({values})",
    )
