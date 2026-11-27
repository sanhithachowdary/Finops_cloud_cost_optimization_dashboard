"""Thread-safe, idempotent in-memory store for ingested billing data.

* A batch is identified by the SHA-256 of its raw bytes: re-submitting the
  same file is a no-op that returns the original report (idempotency, NFR-4).
* Row-level fingerprints prevent the same line item being counted twice
  across different files.
* Each batch report (including quarantined rows) is written to the evidence
  directory so that rejected data is retained, never silently lost.

The store is process-local by design (Community Edition has no database);
see ``docs/limitations-and-assumptions.md``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from finops_engine.engine_config import EngineConfig, get_engine_config
from finops_engine.health import health_registry
from finops_engine.ingestion.validator import ValidationResult, is_attributed, parse_csv_text, validate_rows
from finops_engine.optimization.models import UtilizationSample
from finops_engine.schema.focus_spec import FocusRecord

logger = logging.getLogger("finops.ingestion")


class BatchRejectedError(ValueError):
    """The whole batch is unusable (unsupported schema version, unparseable file …)."""


def _evidence_root() -> Path | None:
    raw = os.getenv("FINOP_EVIDENCE_DIR", "output/evidence")
    return Path(raw) / "ingestion" if raw else None


class IngestionStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.records: list[FocusRecord] = []
        self.fingerprints: set[str] = set()
        self.identities: dict[str, float] = {}
        self.attribution: dict[str, tuple[str, str]] = {}
        self.conflicting_resource_ids: set[str] = set()
        self.utilization: dict[str, UtilizationSample] = {}
        self.batches: dict[str, dict[str, Any]] = {}
        self.batch_order: list[str] = []

    # ------------------------------------------------------------------ ingest
    def ingest_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        raw_bytes: bytes,
        source_name: str,
        source_type: str,
        schema_version: str | None,
        config: EngineConfig | None = None,
        utilization: list[UtilizationSample] | None = None,
    ) -> dict[str, Any]:
        cfg = config or get_engine_config()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        batch_id = digest[:16]
        with self._lock:
            if batch_id in self.batches:
                replay = dict(self.batches[batch_id])
                replay["idempotent_replay"] = True
                logger.info("batch %s already ingested; idempotent replay", batch_id)
                return replay

            version = schema_version or "1.0"
            if version not in cfg.ingestion.supported_schema_versions:
                report = self._base_report(batch_id, digest, source_name, source_type, version, cfg, len(rows))
                report.update(
                    status="rejected",
                    rejection_reason=f"unsupported schema version {version!r}; "
                    f"supported: {list(cfg.ingestion.supported_schema_versions)}",
                )
                self._persist(report, quarantined=[])
                health_registry.record_failure("ingestion", report["rejection_reason"], degraded_only=True)
                raise BatchRejectedError(report["rejection_reason"])

            result = validate_rows(
                rows,
                cfg.ingestion,
                seen_fingerprints=self.fingerprints,
                known_attribution=self.attribution,
                known_identities=self.identities,
            )
            report = self._build_report(batch_id, digest, source_name, source_type, version, cfg, result)

            # Commit atomically: either the whole validated batch is applied or nothing.
            self.records.extend(result.accepted)
            self.fingerprints.update(result.fingerprints)
            self.identities.update(result.identities)
            for rec in result.accepted:
                if rec.resource_id and rec.resource_id not in self.attribution:
                    self.attribution[rec.resource_id] = (rec.provider_name.value, rec.sub_account_id)
            self.conflicting_resource_ids |= result.conflicting_resource_ids
            for sample in utilization or []:
                self.utilization[sample.resource_id] = sample
            self.batches[batch_id] = report
            self.batch_order.append(batch_id)

        self._persist(report, quarantined=[q.to_dict() for q in result.quarantined])
        self._update_health(report, cfg)
        return report

    def add_utilization(self, samples: list[UtilizationSample]) -> int:
        with self._lock:
            for sample in samples:
                self.utilization[sample.resource_id] = sample
            return len(samples)

    # ----------------------------------------------------------------- queries
    def snapshot_records(self) -> list[FocusRecord]:
        with self._lock:
            return list(self.records)

    def snapshot_utilization(self) -> list[UtilizationSample]:
        with self._lock:
            return list(self.utilization.values())

    def reports(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return [self.batches[b] for b in self.batch_order[-limit:]][::-1]

    def quality_summary(self) -> dict[str, Any]:
        with self._lock:
            total = sum(abs(r.billed_cost) for r in self.records)
            attributed = sum(abs(r.billed_cost) for r in self.records if is_attributed(r))
            received = sum(b.get("rows_received", 0) for b in self.batches.values())
            quarantined = sum(b.get("rows_quarantined", 0) for b in self.batches.values())
            return {
                "batches": len(self.batches),
                "records": len(self.records),
                "rows_received_total": received,
                "rows_quarantined_total": quarantined,
                "quarantine_ratio": round(quarantined / received, 4) if received else None,
                "attributed_cost_share": round(attributed / total, 4) if total else None,
                "conflicting_resource_ids": sorted(self.conflicting_resource_ids),
                "utilization_samples": len(self.utilization),
            }

    def clear(self) -> None:
        with self._lock:
            self.__init__()  # type: ignore[misc]

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _base_report(
        batch_id: str,
        digest: str,
        source_name: str,
        source_type: str,
        version: str,
        cfg: EngineConfig,
        rows: int,
    ) -> dict[str, Any]:
        return {
            "batch_id": batch_id,
            "input_sha256": digest,
            "source_name": source_name[:200],
            "source_type": source_type,
            "schema_version": version,
            "config_version": cfg.version,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "rows_received": rows,
            "idempotent_replay": False,
        }

    def _build_report(
        self,
        batch_id: str,
        digest: str,
        source_name: str,
        source_type: str,
        version: str,
        cfg: EngineConfig,
        result: ValidationResult,
    ) -> dict[str, Any]:
        report = self._base_report(batch_id, digest, source_name, source_type, version, cfg, result.rows_received)
        reason_counts: dict[str, int] = {}
        for q in result.quarantined:
            for reason in q.reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        accepted_cost = result.accepted_cost
        quarantined_cost = result.quarantined_cost
        attributed_cost = sum(abs(r.billed_cost) for r in result.accepted if is_attributed(r))
        abs_total = sum(abs(r.billed_cost) for r in result.accepted)
        ratio = (len(result.quarantined) / result.rows_received) if result.rows_received else 0.0
        status = "accepted" if not result.quarantined else "accepted_with_quarantine"
        if result.rows_received and not result.accepted and not result.duplicate_rows:
            status = "rejected"
        report.update(
            status=status,
            rows_accepted=len(result.accepted),
            rows_quarantined=len(result.quarantined),
            rows_duplicate=len(result.duplicate_rows),
            rows_unparseable_cost=result.unparseable_cost_rows,
            reconciliation={
                "accepted_cost_usd": round(accepted_cost, 6),
                "quarantined_cost_usd": round(quarantined_cost, 6),
                "duplicate_cost_usd": round(result.duplicate_cost, 6),
                "parsed_input_cost_usd": round(accepted_cost + quarantined_cost + result.duplicate_cost, 6),
                "note": "parsed_input = accepted + quarantined + duplicate; rows with unparseable cost "
                "are counted in rows_unparseable_cost and quarantined",
            },
            quarantine_reasons=reason_counts,
            warnings=dict(result.warnings),
            transformations=dict(result.transformations),
            out_of_order_rows=result.out_of_order_rows,
            attribution={
                "attributed_cost_share": round(attributed_cost / abs_total, 4) if abs_total else None,
                "unattributed_rows": sum(1 for r in result.accepted if not is_attributed(r)),
                "conflicting_resource_ids": sorted(result.conflicting_resource_ids),
            },
            quarantine_ratio=round(ratio, 4),
            safe_mode_triggered=ratio > cfg.ingestion.quarantine_ratio_safe_mode,
        )
        return report

    def _update_health(self, report: dict[str, Any], cfg: EngineConfig) -> None:
        if report["safe_mode_triggered"]:
            reason = (
                f"batch {report['batch_id']} quarantine ratio {report['quarantine_ratio']} exceeds "
                f"{cfg.ingestion.quarantine_ratio_safe_mode}"
            )
            health_registry.enter_safe_mode("ingestion_quality", reason)
            health_registry.record_failure("ingestion", reason, degraded_only=True)
        elif report["rows_quarantined"] or report["attribution"]["conflicting_resource_ids"]:
            health_registry.record_success(
                "ingestion", f"batch {report['batch_id']} accepted with quarantine/conflicts", degraded=True
            )
        else:
            health_registry.clear_safe_mode("ingestion_quality")
            health_registry.record_success("ingestion", f"batch {report['batch_id']} accepted")

    @staticmethod
    def _persist(report: dict[str, Any], quarantined: list[dict[str, Any]]) -> None:
        root = _evidence_root()
        if root is None:
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
            payload = {"report": report, "quarantined_rows": quarantined}
            (root / f"batch-{report['batch_id']}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("could not persist ingestion evidence for batch %s", report["batch_id"], exc_info=True)


ingestion_store = IngestionStore()


def ingest_csv_file(
    path: str | os.PathLike[str], store: IngestionStore, config: EngineConfig | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read a FOCUS CSV export from disk and ingest it into ``store``; returns (batch report, parsed rows)."""
    cfg = config or get_engine_config()
    path = Path(path)
    raw = path.read_bytes()
    rows = parse_csv_text(raw.decode("utf-8-sig"), cfg.ingestion.max_rows_per_batch)
    report = store.ingest_rows(
        rows, raw_bytes=raw, source_name=path.name, source_type="focus_csv", schema_version="1.0", config=cfg
    )
    return report, rows
