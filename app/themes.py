from __future__ import annotations

from collections.abc import Mapping

DEFAULT_THEME = "institutional"

# Paletas reconhecidas por projetos open source amplamente adotados no GitHub.
# As cores de cada tema são implementadas localmente em app.css para manter
# CSP, disponibilidade offline e a identidade dos componentes do aplicativo.
THEME_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("institutional", "Institucional", "Verde institucional e neutros claros"),
    ("dracula", "Dracula", "Escuro, alto contraste e acentos vibrantes"),
    ("nord", "Nord", "Azul ártico, sóbrio e confortável"),
    ("catppuccin", "Catppuccin", "Pastéis escuros e suaves"),
    ("solarized", "Solarized", "Azul profundo com acentos solares"),
)
THEME_IDS = frozenset(theme_id for theme_id, _, _ in THEME_OPTIONS)


def parse_theme(form: Mapping[str, str]) -> str:
    theme = form.get("theme", "").strip().lower()
    if theme not in THEME_IDS:
        raise ValueError("Selecione um tema válido para a aplicação.")
    return theme
