from __future__ import annotations

from dataclasses import asdict

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Broker, Market, OptionType, Ticker
from app.reference_data import parse_broker, parse_ticker
from app.routes import bp
from app.routes.helpers import (
    broker_records,
    option_contracts,
    option_expirations,
    ticker_records,
)


@bp.get("/tables")
def tables() -> str:
    return render_template(
        "tables.html",
        brokers=broker_records(),
        tickers=ticker_records(),
        markets=Market,
        contracts=option_contracts(),
        expirations=option_expirations(),
        option_types=OptionType,
    )


@bp.post("/tables/brokers")
def create_broker() -> ResponseReturnValue:
    try:
        data = parse_broker(request.form)
        duplicate = db.session.scalar(
            select(Broker).where(
                (func.lower(Broker.name) == data.name.lower())
                | (func.lower(Broker.acronym) == data.acronym.lower())
            )
        )
        if duplicate is not None:
            raise ValueError("O nome ou a sigla da corretora já está cadastrado.")
        db.session.add(Broker(**asdict(data)))
        db.session.commit()
        flash("Corretora adicionada.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("portfolio.tables"))


@bp.post("/tables/brokers/<int:broker_id>")
def update_broker(broker_id: int) -> ResponseReturnValue:
    broker = db.get_or_404(Broker, broker_id)
    try:
        data = parse_broker(request.form)
        duplicate = db.session.scalar(
            select(Broker).where(
                (func.lower(Broker.name) == data.name.lower())
                | (func.lower(Broker.acronym) == data.acronym.lower()),
                Broker.id != broker.id,
            )
        )
        if duplicate is not None:
            raise ValueError("O nome ou a sigla da corretora já está cadastrado.")
        broker.name = data.name
        broker.acronym = data.acronym
        db.session.commit()
        flash("Corretora atualizada.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("portfolio.tables"))


@bp.post("/tables/brokers/<int:broker_id>/delete")
def delete_broker(broker_id: int) -> ResponseReturnValue:
    broker = db.get_or_404(Broker, broker_id)
    db.session.delete(broker)
    try:
        db.session.commit()
        flash("Corretora excluída.", "success")
    except IntegrityError:
        db.session.rollback()
        flash(
            "A corretora não pode ser excluída enquanto possuir posições, "
            "transações ou proventos vinculados.",
            "error",
        )
    return redirect(url_for("portfolio.tables"))


@bp.post("/tables/tickers")
def create_ticker() -> ResponseReturnValue:
    try:
        data = parse_ticker(request.form)
        if db.session.scalar(select(Ticker).where(Ticker.symbol == data.symbol)) is not None:
            raise ValueError("Esse ticker já está cadastrado.")
        db.session.add(Ticker(**asdict(data)))
        db.session.commit()
        flash("Ticker adicionado.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("portfolio.tables"))


@bp.post("/tables/tickers/<int:ticker_id>")
def update_ticker(ticker_id: int) -> ResponseReturnValue:
    ticker = db.get_or_404(Ticker, ticker_id)
    try:
        data = parse_ticker(request.form)
        duplicate = db.session.scalar(
            select(Ticker).where(Ticker.symbol == data.symbol, Ticker.id != ticker.id)
        )
        if duplicate is not None:
            raise ValueError("Esse ticker já está cadastrado.")
        for key, value in asdict(data).items():
            setattr(ticker, key, value)
        db.session.commit()
        flash("Ticker atualizado.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("portfolio.tables"))


@bp.post("/tables/tickers/<int:ticker_id>/delete")
def delete_ticker(ticker_id: int) -> ResponseReturnValue:
    ticker = db.get_or_404(Ticker, ticker_id)
    db.session.delete(ticker)
    try:
        db.session.commit()
        flash("Ticker excluído.", "success")
    except IntegrityError:
        db.session.rollback()
        flash(
            "O ticker não pode ser excluído enquanto possuir posições, "
            "transações ou proventos vinculados.",
            "error",
        )
    return redirect(url_for("portfolio.tables"))
