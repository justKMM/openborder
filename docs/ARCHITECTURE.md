# Architecture

Design goal: **the simplest pipeline that is deterministic, explainable, and actually imports into Dash0.** Delivery speed comes before extensibility. We keep a small IR because the brief asks for one and because it keeps the parser separate from the generators. There is no plugin system and no abstract base classes until a second vendor exists.

## Pipeline

```
 Datadog API ─┐
 JSON files  ─┴─► sources/datadog ──► IR (Dashboard, Panel, AlertRule, query Expr)
                   │  widgets.py / monitors.py   structure
                   │  query_parser.py            DD query string → DD AST
                   │  lower.py + mappings/       DD AST → IR Expr   ← tiers, findings, trace
                   ▼
                validate.py   structure · promtool · Dash0 limits
                   ▼
                targets/      promql.py · perses.py · dash0_rules.py
                   ▼
                ./migration/  perses/*.json · check-rules.json · plan.json · migration.log
                   ▼  (openborder apply)
                dash0_client.py ──► POST /api/import/dashboard (one per dashboard)
                                    POST /api/import/check-rules (one atomic batch)
```

`plan` runs every stage except the last. `apply` reads only `./migration/`. It never re-translates, so what you reviewed is exactly what gets imported.

**The core is a pure library.** `openborder.core.plan(dashboards: list[dict], monitors: list[dict], options) -> Plan` runs in memory. It takes raw Datadog JSON and returns the artifacts as `{relative_path: bytes}` plus the findings. It does no disk, environment, network, or Docker access. The CLI is a thin shell around it: it fetches inputs, calls the core, and writes or imports the result. This keeps the core easy to test, and it means a GUI could reuse it later (see [Future: GUI](#future-gui)).

## Package layout

```
cli/                         # all CLI logic lives here; run from this directory
  requirements.txt
  openborder/                # the Python package → `python -m openborder <command>`
    __main__.py              # entry point
    core.py                  # pure entry points: plan(), explain(); no I/O
    cli.py                   # CLI shell: init, config, discover, plan, apply, explain
    config.py                # credentials/settings in cli/.env (shell-side; never imported by core)
    ir.py                    # all IR models (pydantic)
    sources/datadog/
      client.py              # Datadog API (httpx): list/get dashboards, monitors
      files.py               # load exported JSON
      query_parser.py        # hand-written recursive-descent parser → DD AST
      lower.py               # DD AST → IR Expr; one function per rule id
      widgets.py             # DD widget/layout/template vars → IR Panel/Variable
      monitors.py            # DD monitor → IR AlertRule
    mappings/datadog/redis.yaml
    targets/
      promql.py              # IR Expr → PromQL string (only place that emits PromQL text)
      perses.py              # IR Dashboard → Perses JSON (Dash0 dialect)
      dash0_rules.py         # IR AlertRule → PrometheusAlertRule JSON
    validate.py
    dash0_client.py
    report.py                # Rich tree, coverage score, migration.log
  tests/
    golden/                  # *.dd.txt → *.promql, dashboards/*.dd.json → *.perses.json
demo/                        # docker-compose: redis, load generator, otel-collector (repo root)
```

`backend/` and `frontend/` folders are reserved for a possible GUI later. They don't exist yet.

## IR

The query IR is deliberately **PromQL-shaped**. PromQL is the only target, so a "neutral" query algebra would be over-engineering. Future source adapters lower into the same nodes.

```python
class Tier(IntEnum):            # ordered: worst() = max()
    EXACT = 0; EQUIVALENT = 1; APPROXIMATE = 2; MANUAL = 3

class Finding(BaseModel):
    tier: Tier
    rule_id: str                # e.g. "dd.fn.top", "mapping.unmapped-metric"
    message: str                # human explanation of the difference
    source_ref: str             # e.g. "dashboard:abc/widget:123/query1"

class TraceStep(BaseModel):     # powers `openborder explain`
    rule_id: str; tier: Tier; before: str; after: str

# --- query expressions ---
Matcher(label, op: Literal["=", "!=", "=~", "!~"], value)
Selector(metric: str, matchers: list[Matcher])          # → {otel_metric_name="…", …}
RangeFn(fn: str, arg: Expr, window: str)                 # rate/increase/avg_over_time…; window may be "$__rate_interval"
Subquery(arg: Expr, range: str, step: str | None)
Aggregate(op: str, by: list[str], arg: Expr, param: Expr | None)   # sum/avg/min/max/topk…
BinOp(op: str, lhs: Expr, rhs: Expr, on: list[str] | None)
Call(fn: str, args: list[Expr])                          # abs, clamp_min, log10…
Offset(arg: Expr, duration: str)
Number(value: float)

class Translation(BaseModel):   # result of lowering one DD query/formula
    expr: Expr | None           # None ⇔ tier == MANUAL
    tier: Tier                  # worst tier over findings
    findings: list[Finding]
    trace: list[TraceStep]
    original: str

# --- structure ---
GridPos(x, y, w, h)                                      # already in Perses 24-col units
Panel(key, title, description, kind, queries: list[Translation], grid, options, tier, findings, original: dict)
Section(title, collapsed, panel_keys)                    # from DD group widgets
Variable(name, label, label_name, default, allow_all, allow_multiple, tier, findings)
Dashboard(source_id, title, description, variables, panels, sections, findings, original: dict)
AlertRule(source_id, name, expr, thresholds: {degraded, failed}, interval, for_, keep_firing_for,
          labels, annotations, enabled, tier, findings, original: dict)
```

Rules:
- `original` is always the untouched source JSON or string. Generators copy references to it into their output.
- Tiers move upward only: a node's tier is the worst tier among its children and findings.
- Translation problems are **findings, not exceptions** (see [CONTRIBUTING.md](../CONTRIBUTING.md#error-handling)).

## Query translation

Three stages, each testable on its own:

1. **Parse** (`query_parser.py`): a Datadog query string becomes a Datadog AST, kept close to Datadog's grammar: space aggregation, metric, tag filter (including `$var`, wildcards, `!`, `OR`/`IN`), `by {}`, method chain (`.rollup()`, `.as_rate()`, `.as_count()`, `.fill()`), functions, arithmetic, and `queryN` references in formulas. A parse error becomes a ✗ finding that records the error position.
2. **Lower** (`lower.py`): the Datadog AST becomes an IR expression. **This is the only place semantics are decided.** Each rule is a small function with a fixed `rule_id` and tier. It appends a `TraceStep`, and a `Finding` when the tier is above exact. The metric and tag mapping is applied here.
3. **Print** (`targets/promql.py`): the IR expression becomes PromQL text with minimal, deterministic parentheses.

Worked example (Redis - Overview, "Keyspace hits"):

```
DD:      avg:redis.stats.keyspace_hits{$scope,$host}
AST:     Query(space_agg=avg, metric=redis.stats.keyspace_hits, filters=[Var(scope), Var(host)])
trace:   mapping.metric        ✓  redis.stats.keyspace_hits → redis.keyspace.hits
         mapping.transform     ≈  DD per-second gauge ↔ OTel cumulative counter → rate()
         dd.var.prefixed       ✓  $host → host_name=~"$host"
         dd.var.free-scope     ~  $scope has no tag prefix; dropped (default view unchanged)
         dd.space-agg          ✓  avg → avg()
PromQL:  avg(rate({otel_metric_name="redis.keyspace.hits", host_name=~"$host"}[$__rate_interval]))
```

### Mapping files

The mapping is **data, not code**. `metric_type` in Datadog's `metadata.csv` is not enough: 270 of the 271 Redis metrics are labelled `gauge`, including per-second rates. So each entry states its transform explicitly.

```yaml
integration: redis
otel_receiver: redisreceiver          # opentelemetry-collector-contrib
tags:                                 # DD tag key → Dash0 PromQL label
  host: host_name
metrics:
  redis.mem.used:
    otel: redis.memory.used
    transform: none                   # none | rate | increase
    tier: exact
  redis.stats.keyspace_hits:
    otel: redis.keyspace.hits
    transform: rate
    tier: equivalent
    note: DD agent reports a per-second rate; OTel exports a cumulative counter.
  redis.cpu.sys:
    otel: redis.cpu.time
    matchers: {state: sys}
    transform: rate
    tier: equivalent
  redis.info.latency_ms:
    otel: null                        # no OTel equivalent → mapping.unmapped-metric (~)
```

Unmapped metrics pass through as `{otel_metric_name="<datadog name>"}` with an `approximate` finding. The panel stays structurally correct but will probably show no data, and the report says so.

## Dashboards → Perses

- **Layout:** Datadog's 12-column `ordered` grid becomes Perses's 24-column `Grid` (x and width ×2). Datadog `group` widgets become Perses grid sections with a title. The height scale is calibrated in the spike.
- **Template variables:** `$host` (prefix `host`) becomes a Perses `ListVariable` using `PrometheusLabelValuesVariable` on the mapped label, with "allow all" mapped to `.*`. In queries, `{$host}` becomes `host_name=~"$host"`.
- **Widgets:** see [COMPATIBILITY.md](COMPATIBILITY.md#widgets). Untranslatable widgets become a `Markdown` placeholder in the same grid position. It contains the tier, the reason, and the original query.
- **Panel descriptions:** for panels that aren't exact, the description says how the translation differs ("≈ rate() over $__rate_interval instead of agent-computed per-second rate").

> ⚠ **Verify in the hour-0 spike:** Dash0's panel plugin names, query plugin names, datasource reference, support for `$__interval`, `$__rate_interval`, and `$__range`, and how Markdown panels render. The reference is a dashboard built in the Dash0 UI and fetched over the API.

## Monitors → Dash0 check rules

The Datadog monitor query `<time_agg>(<window>):<metric query> <comparator> <critical>` becomes:

```yaml
name:        "[Redis] High memory consumption"
expression:  avg_over_time((100 * avg({otel_metric_name="redis.memory.used"})
                / avg({otel_metric_name="redis.maxmemory"}))[5m:1m]) > $__threshold
thresholds:  { degraded: 70, failed: 90 }        # DD warning → degraded, critical → failed
interval:    1m
for:         0s
labels:      { integration: redisdb, openborder_tier: equivalent }
annotations: { summary: <name>, description: <message, converted>,
               openborder_source: "datadog:monitor:<id>", openborder_original_query: <query> }
enabled:     true                                 # false when tier ≥ approximate
```

- **Time aggregation:** the window aggregation is pushed down to `*_over_time` when the inner query is a single selector and the result is identical (`max`/`max`, `min`/`min`, `sum`/`sum`). Otherwise it is wrapped in a subquery (≈).
- **Message templates:** `{{value}}` becomes `{{ $value }}`; `{{host.name}}` becomes `{{ $labels.host_name }}`; `{{#is_alert}}` and `{{#is_warning}}` blocks are flattened, with a note. `@`-mentions are removed and reported, because notification routing has to be set up by hand in Dash0.
- **Health colouring:** Dash0 colours services only when result series keep `service_name`. Datadog queries without `by {service}` produce free-standing checks. The report notes this but it doesn't change the tier.

> ⚠ **Verify:** how `$__threshold` behaves with `<` comparators (the Dash0 docs say "the higher value maps to critical"), and whether client-supplied rule `id`s make a repeated bulk import idempotent.

## Validation (`validate.py`)

1. **Structure:** check the generated JSON against our pydantic models of the Dash0 request bodies (`kind: Dashboard`, required rule fields).
2. **PromQL:** write every expression to a temporary rule file, replacing `$__threshold` with `0`, `$__rate_interval`/`$__interval`/`$__range` with `5m`, and `$var` with `.*`, then run `promtool check rules` in the `prom/prometheus` Docker image. If Docker is missing, warn and skip.
3. **Dash0 limits:** at most 1000 rules per bulk request and a 2 MB body. Also run a local pre-flight check on every rule, because one invalid item rejects the entire batch.

## Determinism

- The same input and the same OpenBorder version always produce byte-identical artifacts.
- JSON is written with `sort_keys=True` and a fixed indent. Lists keep the source order.
- IDs are derived, never random: `uuid5(OPENBORDER_NS, "datadog:dashboard:<id>")`, used as `metadata.dash0Extensions.id` and as the check rule `id`. A repeated `apply` overwrites in place.
- Nothing time-dependent appears in the artifacts. `plan.json` records the timestamp separately, and it is excluded from the hashes.

## Plan artifacts (`./migration/`)

```
plan.json            # version, source, items[{source_ref, kind, artifact, sha256, tier, enabled, findings, trace}], summary
perses/<slug>.json   # one Dash0 dashboard import body per dashboard
check-rules.json     # {"items": [...]}, the exact bulk import body
migration.log        # JSON lines: one finding per line (the "logs" the brief asks for)
```

`apply` imports the files listed in `plan.json`. If a file's hash changed, meaning it was edited by hand, `apply` warns and imports the edited version.

## Dash0 integration (verified from the docs)

| Endpoint | Notes |
|---|---|
| `POST /api/import/dashboard` | Body `{kind: "Dashboard", metadata, spec}`. `metadata.dash0Extensions.id` set → overwrite; not set or unknown → create. Bearer auth, `?dataset=`. |
| `POST /api/import/check-rules` | `{items: [...]}`, at most 1000, **atomic** (one invalid item rejects the whole batch), 2 MB body. Rule fields: `id, name, expression, thresholds{degraded, failed}, interval, for, keep_firing_for, labels, annotations, enabled`. |

Configuration: `DASH0_AUTH_TOKEN`, `DASH0_API_URL` (default `https://api.eu-west-1.aws.dash0.com`), `DASH0_DATASET` (default `default`). Datadog: `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE` (default `datadoghq.eu`).

These are stored by `openborder init` / `config set` in `cli/.env` (gitignored; override the location with `OPENBORDER_ENV_FILE`). `config.load_settings()` resolves each value as real environment variable, then file, then default. The clients receive these as plain arguments from the CLI shell; they never read the environment themselves. The `.env` parser is about 30 lines of standard library, so we don't depend on `python-dotenv`.

## Future: GUI

Not built, and not planned for the hackathon build. The idea is the way AWS offers both a CLI and a console: a GUI for less technical users, backed by the same core.

- A `backend/` service would expose `plan`, `explain`, and `apply` over HTTP by calling `openborder.core`. A `frontend/` app would sit on top of it.
- The only obligation today is to keep the core free of I/O (see [CONTRIBUTING.md](../CONTRIBUTING.md#code-organization)), so it can be called from a service as easily as from the CLI.
- If this happens, decisions about the web framework, hosting, and credential handling get made then. User-supplied Datadog and Dash0 keys should never be persisted or logged.

## Adding a vendor (later)

Add `sources/<vendor>/` that produces the same IR, plus `mappings/<vendor>/*.yaml`. The targets, validation, CLI, and report need no changes. We will not build an adapter interface until a second vendor actually exists.
