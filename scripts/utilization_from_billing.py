"""Turn a billing CSV's per-row utilisation column into utilisation samples.

    python -m scripts.utilization_from_billing bill.csv --out output/utilization.json
    python -m scripts.utilization_from_billing bill.csv --post http://127.0.0.1:8765

``--post`` uploads the billing CSV and the derived samples to a running FinOps API
(``/api/v1/ingest/billing`` then ``/api/v1/ingest/utilization``). Set FINOP_API_KEY if the
server requires a key. Only local files are read; nothing is sent anywhere except the URL given.

Assumptions (see ``finops_engine/ingestion/utilization_column.py``): the column is CPU %,
memory is unknown, percentiles are of daily values (proxies), and only compute/database
resources are converted.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from finops_engine.ingestion import parse_csv_text
from finops_engine.ingestion.utilization_column import DEFAULT_COLUMN, samples_from_billing_rows


def _post(url: str, body: bytes, content_type: str) -> dict:
    headers = {"Content-Type": content_type}
    if os.environ.get("FINOP_API_KEY"):
        headers["X-API-Key"] = os.environ["FINOP_API_KEY"]
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    host = urllib.parse.urlsplit(url).hostname or ""
    # Never route a local dashboard through a system proxy.
    handlers = [urllib.request.ProxyHandler({})] if host in {"127.0.0.1", "localhost", "::1"} else []
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{url} -> HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:500]}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach {url}: {exc.reason}. Is run_dashboard.cmd running?") from exc


def _wait_for(url: str, seconds: int) -> None:
    """Poll a health URL until it answers (any HTTP status) or the time runs out."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + seconds
    while True:
        try:
            opener.open(url, timeout=3).close()
            return
        except urllib.error.HTTPError:
            return  # server is up (e.g. 503 while degraded)
        except (urllib.error.URLError, OSError):
            if time.monotonic() >= deadline:
                return  # let the upload report the connection error
            time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path")
    parser.add_argument("--column", default=DEFAULT_COLUMN)
    parser.add_argument("--out", help="write the samples JSON here")
    parser.add_argument("--post", metavar="BASE_URL", help="upload billing + samples, e.g. http://127.0.0.1:8765")
    parser.add_argument("--wait", type=int, default=0, metavar="SECONDS", help="wait for the server to start")
    args = parser.parse_args()

    path = Path(args.csv_path)
    raw = path.read_bytes()
    rows = parse_csv_text(raw.decode("utf-8-sig"), 10_000_000)
    if rows and args.column not in rows[0]:
        raise SystemExit(f"column {args.column!r} not found in {path.name}")
    samples, skipped = samples_from_billing_rows(rows, args.column)
    payload = {"samples": [s.model_dump() for s in samples]}

    print(f"{path.name}: {len(samples)} utilisation samples, {len(skipped)} resources skipped")
    for s in samples:
        print(
            f"  {s.resource_id:<40} {s.resource_type:<8} cpu avg {s.cpu_avg_pct:>6}%  "
            f"p95 {s.cpu_p95_pct:>6}%  over {s.observation_days:g} days"
        )
    for item in skipped:
        print(f"  skipped {item['resource_id']}: {item['reason']}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")

    if args.post:
        _wait_for(args.post.rstrip("/") + "/healthz", args.wait)
        base = args.post.rstrip("/") + "/api/v1/ingest"
        query = urllib.parse.urlencode({"format": "csv", "source_name": path.name})
        billing = _post(f"{base}/billing?{query}", raw, "text/csv")
        print(
            f"billing upload: {billing.get('status')} accepted={billing.get('rows_accepted')} "
            f"quarantined={billing.get('rows_quarantined')} replay={billing.get('idempotent_replay')}"
        )
        util = _post(f"{base}/utilization", json.dumps(payload).encode(), "application/json")
        print(f"utilisation upload: accepted={util.get('accepted')} rejected={util.get('rejected')}")
        print(f"open {args.post.rstrip('/')}/dashboard (choose the 'ingested' data source)")


if __name__ == "__main__":
    main()
