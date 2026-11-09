# Scope

**Context:** a solo developer with a 12-hour budget. The goal is a working end-to-end migration from Datadog to Dash0 with live data, demonstrated on one integration (Redis). Delivery speed comes before long-term maintainability.

## Goal

Show that a real Datadog dashboard and monitor (`integrations-core/redisdb`: "Redis - Overview" and "[Redis] High memory consumption") can be:

1. **pulled** from a Datadog org through its API,
2. **translated** deterministically into Perses, PromQL, and Dash0 check rules, with every difference labelled by tier,
3. **imported** into Dash0 through the official import APIs,
4. **shown working** there, with live Redis data arriving through OpenTelemetry.

## Status (2026-11-08)

| Must-have | State |
|---|---|
| Datadog ingestion: API and `--from-dir` | ☑ done; the API path has run against a real EU org (read-only) |
| `init` / `config` (persistent credentials) | ☑ done |
| `discover` | ☑ done |
| Query parser, lowering rules, PromQL printer | ☑ done, one test row per rule |
| Redis mapping | ☑ done, evidence cited in the YAML |
| Dashboard → Perses, placeholders, layout, variables | ☑ done |
| Monitor → check rule | ☑ done |
| Validation: structure, rule schema, `promtool`, Dash0 size limits | ☑ done (limits are checked by `apply`) |
| `plan` with tree, coverage and findings | ☑ done: the real Redis dashboard gives 32/36 panels migrated, 1/1 monitor armed |
| `apply` (Dash0 import) | ☑ built: confirmation gate, `--dry-run`, hash check, re-validation, stable IDs; tested against a local stand-in server. Real Dash0: an **empty** dashboard imported fine (auth, endpoint, response); panels and check rules **not yet**. |
| `explain` | ☑ done: `--find` on a plan, `--query` on a raw query, `--json` |
| Web backend (`backend/`, FastAPI): discover, plan, review, explain, apply over HTTP for a GUI | ☑ built 2026-11-09 at the user's request, after the CLI milestone: 49 tests against local stand-ins; **not** exercised against a real Datadog or Dash0. No frontend screens yet. See [ARCHITECTURE.md](ARCHITECTURE.md#building-on-top-of-the-cli-backend-and-gui) |
| `demo/` live Redis data | ☑ built; runs locally and its collector output was checked against our mapping (`tests/fixtures/otel`). **Sending to Dash0 has not been run** (needs your endpoint and token) |

**Next step: import the Redis plan (36 panels, 1 alert) into the real Dash0 org.** It is the biggest open risk: no generated panel, variable or check rule has met a real Dash0 yet; see "Not verified against a live Dash0" in [COMPATIBILITY.md](COMPATIBILITY.md).

## What is left (in order)

1. **Import the Redis plan into the real Dash0 org** (`apply`, run by the user) and fix whatever Dash0 rejects. The variable definition's JSON shape is the likeliest surprise (Dash0 doesn't document it).
2. **Run the demo stack against Dash0** (`demo/README.md`) and check that the migrated dashboard fills in. This is also the first real test of `$__rate_interval`, `$__range` and the `^($host)$` variable matcher.
3. Import Datadog's Redis content into the Datadog org so the full Datadog → Dash0 path can be shown over the API instead of from files.
4. Submission: fork `vimouzin/dash0-dashboard-migrator`, push this work to a feature branch, open the PR (`origin` is currently `justKMM/openborder`, not a fork of it).
5. Optional: a host-metrics mapping (`system.*` → OTel `hostmetrics`) so Datadog's default host monitors migrate for real; the static HTML report; the GUI on top of the backend, which is built (see [ARCHITECTURE.md](ARCHITECTURE.md#building-on-top-of-the-cli-backend-and-gui)).

## Must-have (P0)

- Datadog ingestion through the API (`/api/v1/dashboard`, `/api/v1/monitor`) and from exported JSON files (`--from-dir`).
- A Datadog metric-query parser covering the subset in [COMPATIBILITY.md](COMPATIBILITY.md): classic `q` strings and `queries` + `formulas` requests.
- Lowering rules from Datadog to the IR, each with a fixed tier and a recorded trace entry.
- A Redis metric and tag mapping file (Datadog → OTel `redisreceiver`).
- Generators:
  - IR → PromQL text,
  - IR → Perses dashboard (timeseries, query_value, note, group, template variables, layout; `toplist` is a placeholder because Dash0's documented panel kinds have no ranked bar or table),
  - IR → Dash0 check rule (`$__threshold`, degraded/failed thresholds, annotations, labels).
- Placeholder Markdown panels for untranslatable widgets.
- Validation:
  - structural checks,
  - `promtool check rules` (through Docker) with placeholders substituted,
  - Dash0 limits (1000 rules per request, 2 MB body).
- CLI commands: `discover`, `plan`, `apply`, `explain`, with a Rich terminal tree, a coverage score, and a findings log (`migration/migration.log`).
- Deterministic artifacts: sorted keys and stable IDs, so a repeated `apply` overwrites instead of duplicating.
- A `demo/` docker-compose setup: Redis, load generator, and OTel Collector sending to Dash0.
- Golden tests for the query translations and for one full dashboard.

## Stretch (P1, only after the end-to-end milestone)

- A static HTML migration report showing original and translated definitions side by side.
- A companion "no data" rule for monitors with `notify_no_data` (`absent_over_time`, approximate).
- More widget types: `query_table`, `change`, `alert_graph`, `alert_value`.
- A second integration mapping, such as PostgreSQL or host metrics.
- `--from-dir` support for Datadog's Terraform/YAML exports.

## Non-goals

- Source vendors other than Datadog (Cribl, New Relic, and others). The adapter boundary allows them; we won't build them.
- Log, APM, RUM, synthetics, event, SLO, and process queries and monitors. These are reported as ✗ manual.
- Notification channels, routing, `@`-mentions, escalation, and renotify settings. Dash0 configures notifications separately, so these are reported.
- Distribution/percentile metrics and `histogram_quantile` translation.
- Metric mappings for all ~300 integrations. Only Redis is mapped; other metrics pass through and are flagged.
- Continuous sync, drift detection, or managing Dash0 as code. Dash0's import API is meant for one-time migrations.
- Reverse migration (Dash0 → Datadog) and multi-org support.
- Any hosting (Supabase, Vercel, and so on). The CLI came first and is the hackathon deliverable. A web backend and GUI for less technical users were added on top afterwards, at the user's request (see [ARCHITECTURE.md](ARCHITECTURE.md#building-on-top-of-the-cli-backend-and-gui)); the backend has no authentication yet, so it is for localhost.
- Any LLM in the translation, mapping, or validation path.

## Assumptions

- The Datadog org is on the EU site (`datadoghq.eu`) and the Dash0 org is in an EU region. Both are configurable.
- The Redis dashboard and monitor from `integrations-core` are imported into the Datadog org as demo content. Datadog doesn't need any data.
- Dash0 accepts Perses dashboards in its own dialect; the exact plugin and datasource names are verified in the first hour.
- In Dash0's PromQL, OTel metrics are addressed as `{otel_metric_name="..."}`, and dots in attribute names become underscores (`service.name` → `service_name`).

## Timeline (12 h)

| Hours | Work | Exit criterion |
|---|---|---|
| 0–1 | **Spike:** build a dashboard in the Dash0 UI and fetch it over the API (reference schema); import a trivial dashboard and check rule; import the Redis assets into Datadog; get `demo/` sending data to Dash0 | All four APIs answer; Redis metrics visible in Dash0 |
| 1–3 | Datadog query parser, lowering rules, PromQL printer, golden tests | All Redis queries parse and lower |
| 3–4.5 | Widgets → IR → Perses, template variables, layout, Redis mapping | The Redis dashboard JSON imports into Dash0 |
| 4.5–5.5 | Monitors → check rules | The high-memory rule imports into Dash0 |
| 5.5–7 | CLI `plan`/`apply`, Datadog and Dash0 clients, Rich tree, coverage score | **End-to-end milestone:** one command pulls from Datadog and one command lands in Dash0 |
| 7–8.5 | `explain`, fixing panels that render wrong against live data | Every panel shows data or a clear finding |
| 8.5–10 | Stretch goals, or buffer | — |
| 10–12 | README, PR, demo script, pitch, fork and PR to `vimouzin/dash0-dashboard-migrator` | PR open |

If the end-to-end milestone slips past hour 8, all stretch work is cancelled.

## Definition of done (demo checklist)

- [ ] `openborder discover` lists the Redis dashboard and monitor from the live Datadog org. *(works against the live org; the Redis content still has to be imported into Datadog first)*
- [x] `openborder plan` prints a per-panel tier tree and a coverage score, and writes artifacts.
- [x] `openborder explain` shows a rule-by-rule trace for a non-trivial query.
- [ ] `openborder apply` imports the dashboard and the check rule; running it again creates no duplicates. *(an empty dashboard imported; the Redis plan is the next real test)*
- [ ] The dashboard in Dash0 shows live Redis data; the check rule is visible with degraded 70 and failed 90.
- [x] Untranslatable widgets appear as placeholder panels with the original query. *(in the generated dashboard; not yet seen in Dash0)*
- [x] `pytest` passes (231 CLI tests in `cli/`, 49 backend tests in `backend/`).

## Top risks

| Risk | Mitigation |
|---|---|
| Dash0's Perses dialect differs from upstream Perses | Hour-0 spike: use a dashboard exported from Dash0 as the reference schema |
| The OTel label names in Dash0 don't match our mapping | Check against live demo data in the spike; mappings are data, so fixing them is cheap |
| Datadog metric types are misleading (`metadata.csv` says `gauge` for per-second rates) | Write the transform explicitly per metric in the mapping file; never infer it from `metric_type` alone |
| `promtool` rejects `$__threshold`, `$__rate_interval`, or `$var` | Substitute placeholders before validation |
| Running out of time | P0 order puts the end-to-end path first; stretch work is cancelled at hour 8 |
