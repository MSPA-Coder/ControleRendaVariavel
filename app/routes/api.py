from __future__ import annotations

from typing import Any

from flask import current_app, jsonify, request
from flask.typing import ResponseReturnValue
from pydantic import BaseModel, ValidationError

from app import limiter
from app.collector_heartbeat import collector_heartbeat
from app.portfolio import build_portfolio
from app.routes import bp
from app.routes.helpers import (
    poll_interval_seconds,
    positions_query,
    quote_stale_after_seconds,
    rtd_service,
    selected_filters,
)

DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 200


class RtdToggleRequest(BaseModel):
    enabled: bool


def _pagination_params() -> tuple[int, int]:
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    try:
        per_page = int(request.args.get("per_page", str(DEFAULT_PER_PAGE)))
    except ValueError:
        per_page = DEFAULT_PER_PAGE
    page = max(page, 1)
    per_page = min(max(per_page, 1), MAX_PER_PAGE)
    return page, per_page


@bp.route("/api/rtd-service", methods=["GET", "POST"])
@limiter.limit("120 per minute")
def rtd_service_api() -> ResponseReturnValue:
    service = rtd_service()
    try:
        if request.method == "POST":
            payload = request.get_json(silent=True)
            try:
                toggle = RtdToggleRequest.model_validate(payload or {})
            except ValidationError:
                return jsonify(error="Informe o estado booleano 'enabled'."), 400
            if toggle.enabled:
                service.start()
            else:
                service.stop()
        return jsonify(running=service.is_running, available=service.available)
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Não foi possível acessar o coletor RTD: %s", exc)
        return jsonify(error=str(exc), running=False, available=False), 503


@bp.get("/api/collector-heartbeat")
@limiter.limit("120 per minute")
def collector_heartbeat_api() -> ResponseReturnValue:
    return jsonify(
        **collector_heartbeat(
            stale_after_seconds=quote_stale_after_seconds()
        )
    )


def _build_portfolio_payload(page: int, per_page: int) -> dict[str, Any]:
    # Totals and weights are aggregated over the *entire* portfolio (they are
    # meaningless computed over a single page), so pagination is applied only
    # to the row listing, after the full portfolio has been built.
    position_kind, broker, _raw_kind = selected_filters()
    portfolio = build_portfolio(
        positions_query(position_kind, broker),
        stale_after_seconds=quote_stale_after_seconds(),
    )
    total = len(portfolio.positions)
    start = (page - 1) * per_page
    page_views = portfolio.positions[start : start + per_page]

    rows = []
    for view in page_views:
        metric = view.metrics
        rows.append(
            {
                "id": view.position.id,
                "ticker": view.position.ticker,
                "broker": view.position.broker,
                "position_kind": view.position.position_kind.value,
                "quote_status": view.quote_status,
                "current_price": str(metric.current_price) if metric else None,
                "daily_variation": str(metric.daily_variation) if metric else None,
                "result": str(metric.result) if metric else None,
                "return_pct": str(metric.return_pct)
                if metric and metric.return_pct is not None
                else None,
                "annualized_return": (
                    str(metric.annualized_return)
                    if metric and metric.annualized_return is not None
                    else None
                ),
                "current_weight": str(view.current_weight)
                if view.current_weight is not None
                else None,
                "cost_weight": str(view.cost_weight) if view.cost_weight is not None else None,
            }
        )
    return {
        "rows": rows,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": max(1, -(-total // per_page)) if per_page else 1,
        },
        "totals": [
            {
                "currency": total_row.currency,
                "current": str(total_row.current_total),
                "cost": str(total_row.cost_total),
                "result": str(total_row.result_total),
                "return_pct": str(total_row.return_pct)
                if total_row.return_pct is not None
                else None,
                "hhi": str(total_row.hhi) if total_row.hhi is not None else None,
            }
            for total_row in portfolio.currency_totals
        ],
        "brokers": [
            {
                "broker": group.broker,
                "currency": group.currency,
                "current": str(group.current_total),
                "cost": str(group.cost_total),
                "result": str(group.result_total),
                "current_weight": str(group.current_weight)
                if group.current_weight is not None
                else None,
                "cost_weight": str(group.cost_weight) if group.cost_weight is not None else None,
            }
            for group in portfolio.broker_groups
        ],
        "markets": [
            {
                "market": group.market.value,
                "currency": group.currency,
                "current": str(group.current_total),
                "cost": str(group.cost_total),
                "result": str(group.result_total),
                "current_weight": str(group.current_weight)
                if group.current_weight is not None
                else None,
                "cost_weight": str(group.cost_weight) if group.cost_weight is not None else None,
            }
            for group in portfolio.market_groups
        ],
        "poll_interval_seconds": poll_interval_seconds(),
    }


@bp.get("/api/portfolio")
@limiter.limit("120 per minute")
def portfolio_api() -> ResponseReturnValue:
    page, per_page = _pagination_params()
    position_kind, broker, _raw_kind = selected_filters()
    cache: Any = current_app.extensions["portfolio_api_cache"]
    cache_key = f"{position_kind}:{broker}:{page}:{per_page}"
    payload = cache.get_or_set(cache_key, lambda: _build_portfolio_payload(page, per_page))
    return jsonify(**payload)
