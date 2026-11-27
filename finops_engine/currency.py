"""Reporting currency: conversion of connector records and money formatting.

All analytics run in one *base currency* (``ingestion.base_currency`` in the engine config).
Billing exports are converted when they are ingested (``ingestion.fx_rates_to_base``); records
from the mock and live connectors (which bill in USD) are converted here with the same
operator-supplied rates. No live FX feed is used: the rate in the config is the rate.

JSON field names keep their historical ``*_usd`` suffix for API compatibility. The amounts
are in the base currency, which ``/healthz`` and the dashboard report.
"""

from __future__ import annotations

from finops_engine.engine_config import IngestionConfig
from finops_engine.schema.focus_spec import FocusRecord

SYMBOLS = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£", "JPY": "¥"}


class UnsupportedCurrencyError(ValueError):
    """A record's currency has no configured rate to the base currency."""


def currency_symbol(code: str) -> str:
    return SYMBOLS.get(code.upper(), code.upper() + " ")


def format_money(value: float | None, code: str, decimals: int = 2) -> str:
    """``₹1,23,456.78`` for INR (Indian digit grouping), ``$123,456.78`` otherwise."""
    if value is None:
        return "-"
    sign = "-" if value < 0 else ""
    amount = f"{abs(value):,.{decimals}f}"
    if code.upper() == "INR":
        whole, _, frac = amount.replace(",", "").partition(".")
        if len(whole) > 3:
            head, tail = whole[:-3], whole[-3:]
            groups: list[str] = []
            while len(head) > 2:
                groups.insert(0, head[-2:])
                head = head[:-2]
            if head:
                groups.insert(0, head)
            whole = ",".join(groups) + "," + tail
        amount = whole + ("." + frac if frac else "")
    return f"{sign}{currency_symbol(code)}{amount}"


def to_base_currency(records: list[FocusRecord], config: IngestionConfig) -> list[FocusRecord]:
    """Return records in ``config.base_currency``; raises if a currency has no configured rate."""
    base = config.base_currency.upper()
    out: list[FocusRecord] = []
    for rec in records:
        cur = (rec.currency or base).upper()
        if cur == base:
            out.append(rec)
            continue
        rate = config.fx_rates_to_base.get(cur)
        if rate is None:
            raise UnsupportedCurrencyError(
                f"no rate from {cur} to base currency {base}; add it to ingestion.fx_rates_to_base"
            )
        out.append(
            rec.model_copy(
                update={
                    "billed_cost": round(rec.billed_cost * rate, 6),
                    "effective_cost": round(rec.effective_cost * rate, 6),
                    "currency": base,
                }
            )
        )
    return out
