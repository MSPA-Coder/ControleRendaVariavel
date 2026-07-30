from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

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
    try:
        risk_free_rate_annual = Decimal(form.get("risk_free_rate_annual", ""))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Informe uma taxa livre de risco válida.") from exc
    if not MIN_RISK_FREE_RATE_ANNUAL <= risk_free_rate_annual <= MAX_RISK_FREE_RATE_ANNUAL:
        raise ValueError(
            f"A taxa livre de risco deve ficar entre {MIN_RISK_FREE_RATE_ANNUAL:%} e "
            f"{MAX_RISK_FREE_RATE_ANNUAL:%} ao ano."
        )
    return PricingSettingsInput(risk_free_rate_annual)
