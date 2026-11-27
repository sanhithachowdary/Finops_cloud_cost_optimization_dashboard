"""Billing-export validation, normalisation and quarantine (FR-1, NT-1, NT-2).

Input rows may use FOCUS 1.0 column names (``BilledCost``, ``ChargePeriodStart``
…) or this project's own snake_case export names (``billed_cost``,
``billing_period_start`` …).  Every row ends in exactly one bucket:

* **accepted**: normalised to :class:`FocusRecord`
* **quarantined**: rejected with machine-readable reasons; the raw row is kept as evidence
* **duplicate**: an exact repeat of a row already seen (counted once, never double-counted)

so that ``input_total == accepted_total + quarantined_total + duplicate_total``
for every row whose cost could be parsed.  Nothing is dropped silently.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from finops_engine.engine_config import IngestionConfig
from finops_engine.schema.focus_spec import ChargeCategory, CloudProvider, FocusRecord, categorize_service

# Canonical field -> accepted aliases (first match wins).
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "provider_name": ("provider_name", "ProviderName", "provider"),
    "publisher_name": ("publisher_name", "PublisherName"),
    "charge_category": ("charge_category", "ChargeCategory"),
    "billed_cost": ("billed_cost", "BilledCost"),
    "effective_cost": ("effective_cost", "EffectiveCost"),
    "currency": ("currency", "BillingCurrency", "billing_currency"),
    "usage_quantity": ("usage_quantity", "ConsumedQuantity", "UsageQuantity"),
    "usage_unit": ("usage_unit", "ConsumedUnit", "UsageUnit"),
    "service_name": ("service_name", "ServiceName"),
    "service_category": ("service_category", "ServiceCategory"),
    "region_id": ("region_id", "RegionId", "Region"),
    "sub_account_id": ("sub_account_id", "SubAccountId"),
    "resource_id": ("resource_id", "ResourceId"),
    "billing_period_start": ("billing_period_start", "ChargePeriodStart", "charge_period_start"),
    "billing_period_end": ("billing_period_end", "ChargePeriodEnd", "charge_period_end"),
    "tags": ("tags", "Tags"),
}

REQUIRED_FIELDS = (
    "provider_name",
    "service_name",
    "billed_cost",
    "currency",
    "billing_period_start",
    "billing_period_end",
)

PLACEHOLDER_ACCOUNTS = {"", "default-account", "unknown", "none", "null", "n/a"}

UNIT_SYNONYMS = {
    "hours": "Hours",
    "hour": "Hours",
    "hrs": "Hours",
    "hr": "Hours",
    "h": "Hours",
    "gb-hours": "GB-Hours",
    "gb-months": "GB-Months",
    "gb-mo": "GB-Months",
    "gb-month": "GB-Months",
    "gb": "GB",
    "requests": "Requests",
    "count": "Count",
    "vcpu-hours": "vCPU-Hours",
}
# Amounts within this difference are the same line item reported again (e.g. currency rounding
# when the same day appears in a USD and an INR export).
AMOUNT_TOLERANCE = 0.01

# Units converted (with a recorded transformation) to a canonical unit.
UNIT_CONVERSIONS = {"seconds": ("Hours", 1 / 3600.0), "minutes": ("Hours", 1 / 60.0)}

PROVIDER_ALIASES = {
    "aws": CloudProvider.AWS,
    "amazon web services": CloudProvider.AWS,
    "gcp": CloudProvider.GCP,
    "google cloud": CloudProvider.GCP,
    "google": CloudProvider.GCP,
    "azure": CloudProvider.AZURE,
    "microsoft azure": CloudProvider.AZURE,
    "microsoft": CloudProvider.AZURE,
}


@dataclass
class QuarantinedRow:
    row_number: int
    reasons: list[str]
    raw: dict[str, Any]
    billed_cost_parsed: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "reasons": self.reasons,
            "billed_cost_parsed": self.billed_cost_parsed,
            "raw": {
                k: (v if isinstance(v, (str, int, float, bool)) or v is None else str(v)) for k, v in self.raw.items()
            },
        }


@dataclass
class ValidationResult:
    accepted: list[FocusRecord] = field(default_factory=list)
    fingerprints: list[str] = field(default_factory=list)
    # identity_key -> billed amount of every newly accepted line (for cross-batch conflict checks)
    identities: dict[str, float] = field(default_factory=dict)
    quarantined: list[QuarantinedRow] = field(default_factory=list)
    duplicate_rows: list[int] = field(default_factory=list)
    duplicate_cost: float = 0.0
    warnings: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    transformations: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    out_of_order_rows: int = 0
    rows_received: int = 0
    unparseable_cost_rows: int = 0
    conflicting_resource_ids: set[str] = field(default_factory=set)

    @property
    def accepted_cost(self) -> float:
        return round(sum(r.billed_cost for r in self.accepted), 6)

    @property
    def quarantined_cost(self) -> float:
        return round(sum(q.billed_cost_parsed or 0.0 for q in self.quarantined), 6)


# Literal null markers emitted by real exports (e.g. the FinOps Foundation FOCUS
# sample uses the string NULL). "NaN" is deliberately NOT here: a NaN cost is
# malformed, not missing.
NULL_MARKERS = {"null", "none"}

# Optional columns that distinguish separate line items sharing the same
# provider/account/service/resource/period (e.g. different SKUs in one hour).
LINE_DISCRIMINATORS = (
    ("Id", "id", "line_item_id", "LineItemId"),
    ("SkuPriceId", "sku_price_id"),
    ("SkuId", "sku_id"),
    ("ChargeDescription", "charge_description"),
    ("ChargeClass", "charge_class"),
    ("ConsumedQuantity", "usage_quantity", "UsageQuantity"),
)


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if value == "" or value.lower() in NULL_MARKERS:
            return None
    return value


def _pick(row: dict[str, Any], canonical: str) -> Any:
    for alias in FIELD_ALIASES[canonical]:
        if alias in row:
            return _clean(row[alias])
    return None


def _line_discriminator(row: dict[str, Any]) -> str:
    parts = []
    for aliases in LINE_DISCRIMINATORS:
        value = next((_clean(row[a]) for a in aliases if a in row), None)
        parts.append("" if value is None else str(value))
    return "|".join(parts)


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    number = float(str(value).replace(",", "")) if isinstance(value, str) else float(value)
    if math.isnan(number) or math.isinf(number):
        raise ValueError("NaN/Infinity not allowed")
    return number


def _parse_ts(value: Any) -> tuple[datetime, bool]:
    """Parse an ISO-8601 timestamp; returns (utc datetime, assumed_utc)."""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        if len(text) == 10:  # YYYY-MM-DD
            text += "T00:00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc), True
    return dt.astimezone(timezone.utc), False


def _parse_tags(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    text = str(value).strip()
    if not text:
        return {}
    if text.startswith("{"):
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("tags must be an object")
        return {str(k): str(v) for k, v in parsed.items()}
    # key=value;key2=value2
    tags: dict[str, str] = {}
    for part in text.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            tags[k.strip()] = v.strip()
    return tags


def _normalize_provider(value: Any) -> CloudProvider | None:
    if value is None:
        return None
    return PROVIDER_ALIASES.get(str(value).strip().lower())


def _normalize_category(value: Any) -> ChargeCategory | None:
    if value is None:
        return ChargeCategory.USAGE
    for cat in ChargeCategory:
        if cat.value.lower() == str(value).strip().lower():
            return cat
    return None


def row_fingerprint(record: FocusRecord, discriminator: str = "") -> str:
    """Stable identity of a billing line (used for duplicate/idempotency control)."""
    key = (
        discriminator
        + "#"
        + "|".join(
            [
                record.provider_name.value,
                record.sub_account_id,
                record.service_name,
                record.resource_id or "",
                record.region_id,
                record.charge_category.value,
                record.billing_period_start.isoformat(),
                record.billing_period_end.isoformat(),
                f"{record.billed_cost:.6f}",
                record.currency,
            ]
        )
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def identity_key(record: FocusRecord, discriminator: str = "") -> str:
    """Identity *without* the amount: two rows with the same key but different
    amounts are conflicting duplicates.  ``discriminator`` carries optional
    line-item columns (Id, SKU, description, quantity) when the export has them."""
    return (
        discriminator
        + "#"
        + "|".join(
            [
                record.provider_name.value,
                record.sub_account_id,
                record.service_name,
                record.resource_id or "",
                record.region_id,
                record.charge_category.value,
                record.billing_period_start.isoformat(),
                record.billing_period_end.isoformat(),
            ]
        )
    )


def is_attributed(record: FocusRecord) -> bool:
    return bool(record.resource_id) and record.sub_account_id.strip().lower() not in PLACEHOLDER_ACCOUNTS


def parse_csv_text(text: str, max_rows: int) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(reader):
        if idx >= max_rows:
            raise ValueError(f"batch exceeds max_rows_per_batch={max_rows}")
        rows.append({k: v for k, v in row.items() if k is not None})
    return rows


def parse_json_payload(payload: Any, max_rows: int) -> tuple[list[dict[str, Any]], str | None]:
    """Accepts a list of rows or ``{"focus_version": "...", "records": [...]}``."""
    version: str | None = None
    if isinstance(payload, dict):
        version = payload.get("focus_version") or payload.get("schema_version")
        rows = payload.get("records")
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError("JSON payload must be a list of rows or an object with a 'records' list")
    if len(rows) > max_rows:
        raise ValueError(f"batch exceeds max_rows_per_batch={max_rows}")
    if not all(isinstance(r, dict) for r in rows):
        raise ValueError("every record must be a JSON object")
    return rows, (str(version) if version is not None else None)


def validate_rows(
    rows: list[dict[str, Any]],
    config: IngestionConfig,
    *,
    seen_fingerprints: set[str] | None = None,
    known_attribution: dict[str, tuple[str, str]] | None = None,
    known_identities: dict[str, float] | None = None,
    now: datetime | None = None,
) -> ValidationResult:
    """Validate and normalise rows.  Pure function apart from the read-only
    ``seen_fingerprints`` / ``known_attribution`` lookups (cross-batch checks)."""
    result = ValidationResult(rows_received=len(rows))
    seen = set(seen_fingerprints or set())
    attribution = dict(known_attribution or {})
    now = now or datetime.now(timezone.utc)
    future_limit = now + timedelta(hours=config.max_future_skew_hours)

    candidates: list[tuple[int, FocusRecord, dict[str, Any]]] = []
    last_start: datetime | None = None

    for row_number, raw in enumerate(rows, start=1):
        reasons: list[str] = []
        billed: float | None = None
        try:
            billed = _parse_float(_pick(raw, "billed_cost"))
        except (TypeError, ValueError):
            reasons.append("malformed_billed_cost")
        if billed is None:
            result.unparseable_cost_rows += 1

        for req in REQUIRED_FIELDS:
            if _pick(raw, req) is None:
                reasons.append(f"missing_{req}")

        provider = _normalize_provider(_pick(raw, "provider_name"))
        if _pick(raw, "provider_name") is not None and provider is None:
            reasons.append("unknown_provider")

        category = _normalize_category(_pick(raw, "charge_category"))
        if category is None:
            reasons.append("unknown_charge_category")

        effective: float | None = None
        try:
            effective = _parse_float(_pick(raw, "effective_cost"))
        except (TypeError, ValueError):
            reasons.append("malformed_effective_cost")

        usage_qty = 0.0
        try:
            parsed_usage = _parse_float(_pick(raw, "usage_quantity"))
            usage_qty = parsed_usage if parsed_usage is not None else 0.0
        except (TypeError, ValueError):
            reasons.append("malformed_usage_quantity")

        # A usage row whose cost AND quantity are both negative is a reversal /
        # refund line (real Azure exports contain these): accepted, but counted.
        # A negative cost with non-negative quantity (or vice versa) is
        # internally inconsistent and quarantined.
        is_reversal = (
            billed is not None
            and billed < 0
            and usage_qty < 0
            and category not in (ChargeCategory.CREDIT, ChargeCategory.ADJUSTMENT)
        )
        if is_reversal:
            result.warnings["usage_reversal_row_accepted"] += 1
        elif usage_qty < 0 and category not in (ChargeCategory.CREDIT, ChargeCategory.ADJUSTMENT):
            reasons.append("negative_usage_quantity")

        if billed is not None:
            if abs(billed) > config.max_abs_cost_per_row:
                reasons.append("implausible_cost_magnitude")
            if billed < 0 and not is_reversal and category not in (ChargeCategory.CREDIT, ChargeCategory.ADJUSTMENT):
                reasons.append("negative_cost_for_non_credit")
            if billed > 0 and category == ChargeCategory.CREDIT:
                reasons.append("positive_credit")

        start = end = None
        for name in ("billing_period_start", "billing_period_end"):
            value = _pick(raw, name)
            if value is None:
                continue
            try:
                ts, assumed = _parse_ts(value)
                if assumed:
                    result.warnings["naive_timestamp_assumed_utc"] += 1
                if name == "billing_period_start":
                    start = ts
                else:
                    end = ts
            except (TypeError, ValueError):
                reasons.append(f"malformed_{name}")
        if start and end and end <= start:
            reasons.append("period_end_not_after_start")
        if start and start > future_limit:
            reasons.append("timestamp_in_future")

        currency_raw = _pick(raw, "currency")
        currency = str(currency_raw).upper() if currency_raw is not None else None
        fx = 1.0
        if currency and currency != config.base_currency:
            if currency in config.fx_rates_to_base:
                fx = config.fx_rates_to_base[currency]
                result.transformations[f"fx_{currency}_to_{config.base_currency}"] += 1
            else:
                reasons.append("unsupported_currency")

        unit_raw = _pick(raw, "usage_unit")
        unit = "Hours"
        if unit_raw is not None:
            key = str(unit_raw).strip().lower()
            if key in UNIT_SYNONYMS:
                unit = UNIT_SYNONYMS[key]
                if unit != str(unit_raw).strip():
                    result.transformations["unit_synonym_normalised"] += 1
            elif key in UNIT_CONVERSIONS:
                unit, factor = UNIT_CONVERSIONS[key]
                usage_qty *= factor
                result.transformations[f"unit_{key}_to_{unit}"] += 1
            else:
                unit = str(unit_raw).strip()
                configured = {u.lower(): u for u in config.accepted_usage_units}
                if key in configured:
                    unit = configured[key]
                else:
                    result.warnings["unrecognised_usage_unit"] += 1

        tags: dict[str, str] = {}
        try:
            tags = _parse_tags(_pick(raw, "tags"))
        except (TypeError, ValueError, json.JSONDecodeError):
            reasons.append("malformed_tags")

        if reasons:
            parsed_cost = round(billed * fx, 6) if billed is not None else None
            result.quarantined.append(QuarantinedRow(row_number, sorted(set(reasons)), raw, parsed_cost))
            continue

        assert provider is not None and category is not None and start and end and billed is not None
        if effective is None:
            effective = billed
            result.transformations["effective_cost_defaulted_to_billed"] += 1

        service = str(_pick(raw, "service_name"))
        sub_account = str(_pick(raw, "sub_account_id") or "unknown")
        resource_id = _pick(raw, "resource_id")
        record = FocusRecord(
            provider_name=provider,
            publisher_name=str(_pick(raw, "publisher_name") or provider.value),
            charge_category=category,
            billed_cost=round(billed * fx, 6),
            effective_cost=round(effective * fx, 6),
            currency=config.base_currency,
            usage_quantity=round(usage_qty, 6),
            usage_unit=unit,
            service_name=service,
            service_category=str(_pick(raw, "service_category") or categorize_service(service)),
            region_id=str(_pick(raw, "region_id") or "global"),
            sub_account_id=sub_account,
            resource_id=str(resource_id) if resource_id is not None else None,
            billing_period_start=start,
            billing_period_end=end,
            tags=tags,
        )
        if last_start and start < last_start:
            result.out_of_order_rows += 1
        last_start = start
        candidates.append((row_number, record, raw))

    # Duplicate and conflict detection over the candidate set.
    by_identity: dict[str, list[tuple[int, FocusRecord, dict[str, Any]]]] = defaultdict(list)
    for item in candidates:
        by_identity[identity_key(item[1], _line_discriminator(item[2]))].append(item)

    for items in by_identity.values():
        distinct_amounts = {round(rec.billed_cost, 6) for _, rec, _ in items}
        if len(distinct_amounts) > 1:
            # Same line item reported with different amounts: we cannot know
            # which is correct, so all versions are quarantined for review.
            for row_number, rec, raw in items:
                result.quarantined.append(
                    QuarantinedRow(row_number, ["conflicting_duplicate"], raw, round(rec.billed_cost, 6))
                )
            continue
        for row_number, rec, raw_row in items:
            ident = identity_key(rec, _line_discriminator(raw_row))
            earlier = (known_identities or {}).get(ident)
            if earlier is not None:
                # The same line item was accepted from an earlier bill (overlapping exports).
                if abs(earlier - rec.billed_cost) <= AMOUNT_TOLERANCE:
                    result.duplicate_rows.append(row_number)  # same amount (to the cent): count it once
                    result.duplicate_cost = round(result.duplicate_cost + rec.billed_cost, 6)
                else:  # restated with a different amount: never add it on top of the first version
                    result.quarantined.append(
                        QuarantinedRow(
                            row_number, ["conflicting_duplicate_across_batches"], raw_row, round(rec.billed_cost, 6)
                        )
                    )
                continue
            fp = row_fingerprint(rec, _line_discriminator(raw_row))
            if fp in seen:
                result.duplicate_rows.append(row_number)
                result.duplicate_cost = round(result.duplicate_cost + rec.billed_cost, 6)
                continue
            seen.add(fp)
            result.accepted.append(rec)
            result.fingerprints.append(fp)
            result.identities[ident] = rec.billed_cost

    # Attribution conflicts: one resource id reported under different
    # provider/account combinations (within this batch or vs. earlier batches).
    for rec in result.accepted:
        if not rec.resource_id:
            continue
        owner = (rec.provider_name.value, rec.sub_account_id)
        previous = attribution.get(rec.resource_id)
        if previous is None:
            attribution[rec.resource_id] = owner
        elif previous != owner:
            result.conflicting_resource_ids.add(rec.resource_id)

    paired = sorted(
        zip(result.accepted, result.fingerprints, strict=True),
        key=lambda p: (p[0].billing_period_start, p[0].provider_name.value, p[0].service_name),
    )
    result.accepted = [p[0] for p in paired]
    result.fingerprints = [p[1] for p in paired]
    result.quarantined.sort(key=lambda q: q.row_number)
    return result
