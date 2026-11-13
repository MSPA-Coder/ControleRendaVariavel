from __future__ import annotations

from collections.abc import Mapping

DEFAULT_THEME = "light"

# Paletas reconhecidas por projetos open source amplamente adotados no GitHub,
# mais o verde institucional original do projeto (primeiro tema, anterior a
# esta expansao). As cores de cada tema sao implementadas localmente em
# app.css para manter CSP, disponibilidade offline e a identidade dos
# componentes do aplicativo.
THEME_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("institutional", "Institucional", "Verde institucional original"),
    ("light", "Claro", "Claro e neutro"),
    ("dark", "Escuro", "Escuro e focado"),
    ("solarized_light", "Solarized claro", "Baixo contraste, tom quente"),
    ("solarized_dark", "Solarized escuro", "Baixo contraste, tom frio"),
    ("dracula", "Dracula", "Alto contraste"),
    ("nord", "Nord", "Azul frio"),
    ("monokai", "Monokai", "Técnico vibrante"),
    ("gray", "Tons de cinza", "Neutro analítico"),
    ("soft_light", "Claro suave", "Claro confortável"),
    ("soft_dark", "Escuro suave", "Escuro confortável"),
    ("corporate_blue", "Azul corporativo", "Sóbrio e profissional"),
    ("emerald", "Esmeralda", "Verde financeiro"),
)
THEME_IDS = frozenset(theme_id for theme_id, _, _ in THEME_OPTIONS)

# Descrições detalhadas para cada tema (sem ícones/emojis para interface mais prática)
THEME_DESCRIPTIONS = {
    "institutional": "Verde institucional original",
    "light": "Claro e neutro",
    "dark": "Escuro e focado",
    "solarized_light": "Baixo contraste, tom quente",
    "solarized_dark": "Baixo contraste, tom frio",
    "dracula": "Alto contraste",
    "nord": "Azul frio",
    "monokai": "Técnico vibrante",
    "gray": "Neutro analítico",
    "soft_light": "Claro confortável",
    "soft_dark": "Escuro confortável",
    "corporate_blue": "Sóbrio e profissional",
    "emerald": "Verde financeiro",
}


def parse_theme(form: Mapping[str, str]) -> str:
    theme = form.get("theme", "").strip().lower()
    if theme not in THEME_IDS:
        raise ValueError("Selecione um tema válido para a aplicação.")
    return theme


def get_theme_options_dict() -> dict[str, str]:
    """Retorna dicionário de temas para uso em templates."""
    return {theme_id: label for theme_id, label, _ in THEME_OPTIONS}
