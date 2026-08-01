from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.validation import parse_finite_decimal

MIN_RISK_FREE_RATE_ANNUAL = Decimal("0")
MAX_RISK_FREE_RATE_ANNUAL = Decimal("1")  # 100% a.a., limite generoso de sanidade
DEFAULT_RISK_FREE_RATE_ANNUAL = Decimal("0.1075")
"""Taxa de referência inicial (ordem de grandeza da SELIC/CDI); editável em
Configurações. Não é buscada automaticamente de nenhuma fonte externa —
isso fica para uma integração futura (ver Fase G do roadmap)."""


@dataclass(frozen=True, slots=True)
class PricingSettingsInput:
    risk_free_rate_annual: Decimal


def parse_pricing_settings(form: Mapping[str, str]) -> PricingSettingsInput:
    risk_free_rate_annual = parse_finite_decimal(
        form.get("risk_free_rate_annual", ""),
        field_name="uma taxa livre de risco",
    )
    if not MIN_RISK_FREE_RATE_ANNUAL <= risk_free_rate_annual <= MAX_RISK_FREE_RATE_ANNUAL:
        raise ValueError(
            f"A taxa livre de risco deve ficar entre {MIN_RISK_FREE_RATE_ANNUAL:%} e "
            f"{MAX_RISK_FREE_RATE_ANNUAL:%} ao ano."
        )
    return PricingSettingsInput(risk_free_rate_annual)
