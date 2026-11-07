# OpenBorder

> **`terraform plan` for leaving your observability vendor.**

OpenBorder migrates Datadog dashboards and monitors to [Dash0](https://www.dash0.com), using open standards. Dashboards become [Perses](https://perses.dev), queries become PromQL, and alerts become Prometheus alerting rules with Dash0's `$__threshold` extension. Metric names follow OpenTelemetry semantic conventions.

The translation path contains **no LLMs**. Every query passes through a parser and named translation rules, and each rule has a fixed fidelity tier. That makes every translation deterministic, testable, and explainable.

> 🚧 **Status:** hackathon build in progress (thinc! 2026, Dash0 challenge). The commands below describe the target interface. Today only `python -m openborder version` and `--help` work; `discover`, `plan`, `apply` and `explain` exist as stubs that exit with an error. See [docs/SCOPE.md](docs/SCOPE.md) for what is in and out.

## Why

Teams spend years building dashboards and alerts. Over that time those assets get locked into a vendor's query language and JSON format. Even when a better or cheaper platform exists, rebuilding everything by hand takes weeks, and every hand-rewritten alert might quietly page on the wrong thing, or never page at all.

OpenBorder makes the move a repeatable, reviewable process:

1. **Plan:** pull dashboards and monitors from the Datadog API and translate them locally. You see exactly what will change, panel by panel, and how faithful each translation is.
2. **Review:** the plan is a set of plain files (Perses JSON, check rules, `plan.json`). You can diff them, commit them, or edit them by hand.
3. **Apply:** import exactly what was planned into Dash0. Re-running `apply` overwrites in place and never creates duplicates.

## Fidelity tiers

Every translation rule has a fixed tier, listed in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md). A panel or alert takes the tier of its worst rule.

| Tier | Meaning | Dashboards | Alerts |
|---|---|---|---|
| ✓ **exact** | Same meaning | panel generated | imported, enabled |
| ≈ **equivalent** | Known, bounded difference that does not change which series appear or whether an alert fires in practice | panel generated, difference noted in its description | imported, enabled |
| ~ **approximate** | May change which series appear or when an alert fires | panel generated, warning in its description | imported **disabled**, unless `--enable-approximate` |
| ✗ **manual** | No equivalent in Dash0 | placeholder Markdown panel in the same grid position, showing the original query | not imported, listed in the report |

The original Datadog definition is always kept, both in the generated artifacts and in the report. OpenBorder never silently drops functionality or claims an equivalence it hasn't checked.

## Quickstart

**Requirements:** Python 3.12+. Docker is optional; it's used for `promtool` validation and the live-data demo.

```bash
cd cli
python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt

export DD_API_KEY=...            # Datadog → Organization Settings → API Keys
export DD_APP_KEY=...            # Datadog → Organization Settings → Application Keys
export DD_SITE=datadoghq.eu      # default
export DASH0_AUTH_TOKEN=...      # Dash0 → Settings → Auth Tokens
export DASH0_API_URL=https://api.eu-west-1.aws.dash0.com   # default; match your region
export DASH0_DATASET=default     # default

python -m openborder discover                                # list dashboards & monitors
python -m openborder plan --dashboard abc-def-ghi --monitor-tag integration:redisdb
python -m openborder explain --query 'avg:redis.stats.keyspace_hits{*}'
python -m openborder apply ./migration                      # asks for confirmation; --auto-approve to skip
```

To work offline, use `--from-dir PATH` instead of the Datadog API. It reads exported dashboard and monitor JSON, for example from [DataDog/integrations-core](https://github.com/DataDog/integrations-core).

### Example plan output (mockup)

```
Redis - Overview  →  Perses dashboard
  ├─ ✓ Memory used                    avg by (host_name) ({otel_metric_name="redis.memory.used"})
  ├─ ≈ Keyspace hit rate              rate(...) instead of agent-computed per-second rate
  ├─ ~ Top 10 slowest commands        top() ranks over the window; topk over avg_over_time
  └─ ✗ Redis logs (list_stream)       log streams unsupported → placeholder panel
  Coverage: 41/43 panels migrated (✓ 30 · ≈ 8 · ~ 3 · ✗ 2)

Monitors  →  Dash0 check rules
  └─ ≈ [Redis] High memory consumption   degraded 70 · failed 90 · enabled
  Armed: 1/1

Artifacts written to ./migration  ·  run `openborder apply ./migration` to import
```

## Live-data demo

[`demo/`](demo/) contains a `docker-compose` setup with Redis, a load generator, and an OpenTelemetry Collector (`redisreceiver`) that sends metrics to Dash0. With it running, the migrated dashboard shows real data, not just an empty layout.

## Documentation

- [docs/SCOPE.md](docs/SCOPE.md): scope, non-goals, priorities, and timeline
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): pipeline, IR, fidelity model, and Dash0 integration
- [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md): what translates, how, and at which tier
- [CONTRIBUTING.md](CONTRIBUTING.md): development guidelines
