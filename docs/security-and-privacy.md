# Security and Privacy

## What was checked and changed

| Area | Behaviour |
|---|---|
| Authentication | Existing API-key gate (constant-time compare) now covers every new `/api/v1/*` route, including ingestion. `/dashboard` (static shell) and `/healthz` (status + mode only) are public. Tested in `test_api_extended.py::test_auth_boundary_on_new_endpoints`. |
| Authorisation | Single shared key, so there are no per-user roles. This is unchanged and documented as a limitation. |
| CORS | Unchanged: wildcard only in dev mock mode; an allow-list is required in production. |
| Dashboard | Content-Security-Policy `default-src 'self'`, `frame-ancestors 'none'`, `nosniff`, `no-referrer`. No external scripts or fonts. All API data is HTML-escaped before insertion. The API key is kept in page memory only and never written to browser storage. |
| Input validation | Body size limit (Content-Length and streamed check, default 20 MB, returns 413). Row limit (200k). UTF-8 required. CSV/JSON parse errors return 422. Strict pydantic models (`extra="forbid"`) for utilisation and config. Query parameters are constrained by regex, enum or range. No `eval`, no pickle, no file paths taken from user input. |
| Injection | No SQL is built from ingestion input. The existing GCP connector keeps its table-name regex and parameterised dates. |
| Error handling | Global handler returns `incident_id` only. Stack traces stay in server logs (NT-5 test). Ingestion errors describe the problem but do not echo raw payloads. |
| Resource exhaustion | Bounded worker pool (8) with fail-fast saturation, per-call timeouts, bounded retries, bounded event and latency buffers (500 / 10,000), Prometheus labels bounded (no path parameters exist). |
| Secrets | No credentials in code, config or fixtures. `.env*` stays ignored. The new `.env.example` keys contain no secrets. A secret scan of the final tree was run (see `FINAL_IMPLEMENTATION_REPORT.md` §9). |
| Billing data | Treated as sensitive. Quarantined rows (raw values) are written only to `FINOP_EVIDENCE_DIR` (default `output/evidence`, git-ignored). `.gitignore` also blocks `*.billing.csv` and `billing-export*/`. Fixtures in the repo are SYNTHETIC. |
| Cloud access | Nothing in this work contacted a cloud account. Live connectors keep using the SDK default credential chain. Read-only roles are recommended: AWS `ce:GetCostAndUsage`; GCP BigQuery Data Viewer on the billing dataset plus Job User; Azure Cost Management Reader. |

## Dependency notes

- All runtime pins are unchanged and no dependencies were added.
- The cloud workspace could not reach PyPI or vulnerability databases, so no automated CVE
  scan (pip-audit or Dependabot) was run.
- Dependabot is configured in `.github/dependabot.yml`.
- **Recommended:** run `pip install pip-audit && pip-audit -r requirements.txt` on a machine
  with internet access.

## Residual risks

- A single shared API key has no rate limiting, so an authenticated client can trigger
  expensive analyses. Mitigations: the detection cache, the bounded pool, and putting a
  gateway with rate limits in front of the service.
- `/healthz` reveals whether the service is degraded. This is acceptable for probes;
  restrict it at the ingress if that matters to you.
- Quarantine evidence contains raw billing rows. Protect the evidence directory with OS
  permissions and set a retention policy.
