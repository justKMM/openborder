# Architecture

Design goal: **the simplest pipeline that is deterministic, explainable, and actually imports into Dash0.** Delivery speed comes before extensibility. We keep a small IR because the brief asks for one and because it keeps the parser separate from the generators. There is no plugin system and no abstract base classes until a second vendor exists.

**Implementation status:** All commands are built and tested (214 tests): `init`, `config`, `discover`, `plan`, `apply`, `explain`. `apply` has imported one empty dashboard into a real Dash0 org; no panels, variables or check rules have been imported there yet, so the Perses panel dialect and the rule import are still from Dash0's documentation. The demo stack (`demo/`) runs locally and its collector output was checked against our metric mapping; sending to Dash0 has not been run.

## Pipeline

```
 Datadog API ─┐
 JSON files  ─┴─► sources/datadog ──► IR (Dashboard, Panel, AlertRule, query Expr)
                   │  widgets.py / monitors.py   structure, layout, variables
                   │  query_parser.py            DD query string → DD AST
                   │  lower.py + mappings/       DD AST → IR Expr   ← tiers, findings, trace
                   ▼
                targets/      promql.py · perses.py · dash0_rules.py
                   ▼
                validate.py   structure · rule schema          (pure)
                promtool.py   PromQL syntax via Docker         (CLI only)
                   ▼
                ./migration/  perses/*.json · check-rules.json · plan.json · migration.log
                   ▼  (openborder apply)  apply.py: load, verify hashes, re-validate
                dash0_client.py ──► POST /api/import/dashboard (one per dashboard)
                                    POST /api/import/check-rules (one atomic batch)
```

`plan` runs every stage except the last. `apply` reads only `./migration/` and never re-translates, so what you reviewed is exactly what gets imported.

**The core is a pure library.** `openborder.core.plan(dashboards, monitors, options, skipped)` runs in memory: raw Datadog JSON in, artifacts as `{relative_path: bytes}` plus findings out. It does no disk, environment, network or Docker access, which is why `promtool.py` (Docker) and `config.py` (environment) are separate modules that `core` never imports. The CLI is a thin shell: it fetches inputs, calls the core, runs `promtool`, writes the files and prints the report. This keeps the core easy to test, and a GUI could reuse it later (see [Building on top of the CLI](#building-on-top-of-the-cli-backend-and-gui)).

## Package layout

```
cli/                         # all CLI logic lives here; run from this directory
  requirements.txt
  openborder/                # the Python package → `python -m openborder <command>`
    __main__.py              # entry point (forces UTF-8 output so tier symbols never crash a Windows console)
    cli.py                   # CLI shell: init, config, discover, plan, apply, explain
    core.py                  # pure: plan() and render_plan_json(); no I/O
    ir.py                    # IR (frozen dataclasses): Tier, Finding, TraceStep, Expr nodes, Panel, Dashboard, AlertRule
    ids.py                   # stable_id() (uuid5) and slug(): the only source of identifiers
    config.py                # credentials/settings in cli/.env (shell-side; never imported by core)
    report.py                # Rich output: plan tree, coverage, monitors, findings-by-rule, validation
    validate.py              # pure: dashboard structure checks, pydantic model of the check-rule body
    explain.py               # shell-side: explanations (dicts) of a query, panel or monitor; `explain --json` prints them
    apply.py                 # shell-side: load and verify a plan folder for `apply` (hashes, re-validation, Dash0 size limits)
    dash0_client.py          # Dash0 API (httpx): the two import endpoints; https-only, dataset check, error messages
    promtool.py              # `promtool check rules` via Docker (CLI only)
    mappings/
      __init__.py            # load_mappings(): merges every datadog/*.yaml
      datadog/redis.yaml     # metric + tag mapping with cited evidence
    sources/datadog/
      client.py              # Datadog API (httpx), read-only: list/get dashboards, list monitors
      files.py               # load exported JSON from a folder
      query_parser.py        # hand-written tokenizer + recursive-descent parser → Datadog AST
      lower.py               # Datadog AST → IR Expr; one rule per rule_id (see COMPATIBILITY.md)
      widgets.py             # dashboard JSON → IR (widgets, groups, layout, template variables)
      monitors.py            # monitor JSON → IR AlertRule (window, thresholds, message, options)
    targets/
      promql.py              # IR Expr → PromQL string (the only place that emits PromQL)
      perses.py              # IR Dashboard → Dash0 dashboard import body
      dash0_rules.py         # IR AlertRule → Dash0 check-rule import body
  tests/
    conftest.py              # autouse isolation (temp cwd, no credentials), golden-file helper, FakeDash0 (a real local HTTP server)
    fixtures/datadog/redisdb # the real Redis dashboard and monitor from integrations-core
    fixtures/otel/redisreceiver.json  # metrics a real collector 0.165.0 emitted; guards the Redis mapping
    golden/redis/            # expected artifacts for those fixtures
demo/                        # docker-compose: redis, load generator, otel-collector (see demo/README.md)
```

`backend/` and `frontend/` folders are reserved for a possible GUI later. They don't exist.

## IR

Frozen dataclasses (nothing is parsed from untrusted input into them, so there is nothing for a validation library to do). The query IR is deliberately **PromQL-shaped**: PromQL is the only target, so a neutral query algebra would be over-engineering.

```python
class Tier(IntEnum): EXACT=0; EQUIVALENT=1; APPROXIMATE=2; MANUAL=3     # worst of several = max()

Finding(tier, rule_id, message, source_ref)          # something the user must know about
TraceStep(rule_id, tier, before, after)              # every rule that ran, exact ones included; feeds `explain`

# query expressions
Selector(metric, matchers, offset)   RangeFn(fn, arg, window)   Subquery(arg, range, step)
Aggregate(op, by, arg, param)        BinOp(op, lhs, rhs, on, group)   Call(fn, args)
Number(value)                        Placeholder(text)      # e.g. $__threshold

Translation(expr | None, findings, trace, original)  # one Datadog query or formula; tier = worst finding
Panel(key, title, kind, grid, queries, options, findings, trace, original, source_queries, placeholder)
Section(title, panels)   Variable(name, label, default, findings)   Dashboard(..., sections, findings, original)
AlertRule(source_id, name, expr, thresholds, interval, labels, annotations, findings, trace, original, enabled)
```

Rules:
- `original` is the untouched source JSON or string. It ends up in `plan.json`.
- A node's tier is the worst tier among its findings. A rule with tier ✓ records a trace step but no finding.
- Translation problems are **findings, not exceptions**. A MANUAL finding means `expr` is `None`.
- A formula's `Translation` holds only the findings of the formula itself. The queries it references keep their own findings, and a panel's findings are the union of all of them.

## Query translation

Three stages, each testable on its own:

1. **Parse** (`query_parser.py`): tokenizer plus recursive descent into a Datadog-shaped AST: metric queries (space aggregator, metric, tag filters, `by {}`, method chain), formulas (`queryN` references, arithmetic, functions, unary minus), and monitor queries (`avg|max|min|sum(last_N<unit>):<query> <comparator> <number>`). Filters it doesn't understand (`OR`, bare tags) are kept as `RawFilter` so the lowering can refuse them explicitly instead of dropping them. Parse errors carry a position.
2. **Lower** (`lower.py`): the Datadog AST becomes an IR expression. **This is the only place semantics are decided.** Every rule has a fixed `rule_id` and tier, calls `Recorder.step()`, and raises `Unsupported` for anything it cannot translate. `translate()` never raises: parse errors and `Unsupported` become a MANUAL finding for that query only.
3. **Print** (`targets/promql.py`): the IR becomes PromQL text with the minimum parentheses that preserve the AST.

Worked example (`avg:redis.stats.keyspace_hits{$scope,$host}` in a dashboard whose `host` variable has prefix `host`):

```
mapping.metric            ✓  redis.stats.keyspace_hits → redis.keyspace.hits
mapping.transform.rate    ≈  Datadog reports a per-second rate; OTel exports a cumulative counter → rate()
dd.var.free-scope         ✓  $scope dropped (default is *; reported once on the dashboard)
mapping.tag               ✓  host → host_name
dd.var.prefixed           ✓  $host → host_name=~"^($host)$"   (anchored: Dash0's =~ is an unanchored search)
dd.space-agg              ✓  avg
→ avg(rate({otel_metric_name="redis.keyspace.hits", host_name=~"^($host)$"}[$__rate_interval]))     tier ≈
```

Context differences: in a **dashboard** the rate window is `$__rate_interval`, auto rollups use `$__interval`, and `top()` uses `$__range`. In a **monitor** (a Prometheus rule has no such variables) the rate window is the monitor's own window, rollups use `1m`, and `top()` is unavailable.

### Mapping files

The mapping is **data, not code**. `metric_type` in Datadog's `metadata.csv` is unusable for deciding whether a transform is needed (270 of 271 Redis metrics are labelled `gauge`, rates included), so each entry's `transform` comes from the integration's source code and is cited in the YAML.

```yaml
tags:                                  # Datadog tag → label on the OTel series in Dash0 PromQL
  host: host_name
metrics:
  redis.mem.used:        {otel: redis.memory.used}                                # transform: none
  redis.keys.evicted:    {otel: redis.keys.evicted}                               # raw total → no rate()
  redis.stats.keyspace_hits: {otel: redis.keyspace.hits, transform: rate}         # per-second rate → rate()
  redis.cpu.sys:         {otel: redis.cpu.time, matchers: {state: sys}, transform: rate}
  redis.info.latency_ms: {otel: null}                                             # no counterpart → ~
```

Unmapped metrics pass through under their Datadog name with a ~ finding: the panel is structurally correct but will probably show no data, and the report says so. Full table: [COMPATIBILITY.md](COMPATIBILITY.md#redis-metric-mapping-datadog--otel-redisreceiver).

## Dashboards → Perses

Format reference: [Dash0 dashboard source format](https://www.dash0.com/docs/dash0/dashboards/reference-dashboard-source-format). Dash0 selects the data source itself, so panels carry none.

- **Panels:** `timeseries` → `TimeSeriesChart`, `query_value` → `StatChart`, `note` → `Markdown`. Queries are `TimeSeriesQuery` with a `PrometheusTimeSeriesQuery` plugin; the legend is `{{label}}` for the `by` labels, else the Datadog alias.
- **Formulas:** each `queries` entry is translated once into a named expression; each formula is then parsed with those names inlined. Legacy `q` strings (several comma-separated queries per request) are supported too.
- **Failure handling:** if any query or formula of a widget is MANUAL, the whole widget becomes a placeholder. A half-migrated chart that silently omits a series would be worse than an obvious gap.
- **Placeholders:** a `Markdown` panel in the original grid position with the reason, the quoted original queries, and a pointer to `plan.json`.
- **Layout:** each Datadog group is a Perses `Grid` section, in order. The group's inner grid spans the group's own width `W`, so x and width are scaled by `24/W`, with rounding done on the right edge so neighbours stay adjacent. Heights are multiplied by 3 and have a minimum of 3. Groups that Datadog put side by side are stacked (≈, `dd.layout.groups-stacked`).
- **Variables:** a variable with a tag prefix becomes a `ListVariable` (`PrometheusLabelValuesVariable` over the mapped label; "All" is `.*`). A free-form variable has no label to map to, so it is dropped and reported once per dashboard.
- **Description:** every panel that is not ✓ gets a description listing its findings and the original Datadog queries, so the difference is visible inside Dash0.
- **Identity:** `metadata.name` is a slug of the title; `metadata.dash0Extensions.id` is a `uuid5` of the Datadog dashboard id.

## Monitors → Dash0 check rules

`avg(last_5m):100 * avg:redis.mem.used{*} / avg:redis.mem.maxmemory{*} > 90` with `critical: 90, warning: 70` becomes:

```yaml
expression:  avg_over_time((100 * avg({otel_metric_name="redis.memory.used"}) / avg({otel_metric_name="redis.maxmemory"}))[5m:1m]) > $__threshold
thresholds:  { degraded: 70, failed: 90 }        # DD warning → degraded, critical → failed
interval:    1m
labels:      { integration: redisdb }             # from the monitor's tags
annotations: { summary, description (message, converted), openborder_tier, openborder_source, openborder_original_query }
enabled:     true                                 # false when the tier is approximate (override: --enable-approximate)
```

- **Window:** pushed down to `*_over_time` on the selector only when the space and time aggregators match and the inner query is a single aggregated selector (exact). Otherwise the whole expression goes into a subquery at 1-minute resolution (≈), which is the faithful reading of "average of the query's value over the window".
- **Comparator:** `>` and `>=` are exact. `<` and `<=` are ~ because Dash0 documents that the higher threshold maps to `failed`. `==` and `!=` are ✗.
- **Message:** `{{value}}` and `{{host.name}}` are converted to Prometheus template syntax, severity blocks are flattened, recovery/no-data blocks are dropped, `@`-mentions are removed and listed, and any remaining Datadog variable is listed. All of it is a ≈ finding.
- **Not exported:** a MANUAL monitor is listed in the report and `plan.json` (with its original), but not written to `check-rules.json`.

## Validation

1. **Structure** (`validate.py`, pure, run inside `core`): `kind`, `metadata.name`, every layout `$ref` resolves, every panel is placed, items fit the 24-column grid, non-Markdown panels have non-empty queries; check rules are validated by a pydantic model of the documented `PrometheusAlertRule` fields (`extra="forbid"` catches typos; `$__threshold` requires thresholds).
2. **PromQL syntax** (`promtool.py`, CLI only): every generated expression goes into a throw-away rules file, one group each so a failure is attributed to its panel. `$__rate_interval` and `$__interval` become `5m`, `$__range` becomes `1h`, `$__threshold` becomes `0`; `$host` inside a label value is already a valid string. It runs `promtool check rules` from the `prom/prometheus` image. If Docker is unavailable it says so and carries on. Verified to reject broken PromQL and name the offending expression.
3. **Dash0 limits** (not yet checked): at most 1000 rules per request and a 2 MB body. The bulk rule import is atomic, so one invalid rule rejects all of them.

A structure or promtool error makes `plan` exit 1, after writing the artifacts.

## Determinism

- The same input and the same OpenBorder version give byte-identical artifacts. Tested, and covered by golden files.
- JSON is written with `sort_keys=True`, 2-space indent and a trailing newline. Lists keep the source order; files are loaded in sorted order.
- IDs are never random: `uuid5(namespace, "datadog:<kind>:<source id>")` for the dashboard `dash0Extensions.id` and the check-rule `id`. A repeated `apply` will overwrite in place.
- No timestamps in any artifact. `plan.json` does record whether `promtool` ran, which depends on the machine.

## Plan artifacts (`./migration/`)

```
plan.json            # version, source, summary, validation, items[…]: everything `apply` and `explain` need
perses/<slug>.json   # one Dash0 dashboard import body per dashboard
check-rules.json     # {"items": [...]}, the exact bulk import body (only when at least one rule is importable)
migration.log        # JSON lines: one non-exact finding per line (the "logs" the brief asks for)
```

`plan.json` items: for dashboards `{kind, source_ref, title, artifact, sha256, tier, findings, panels[{key, title, kind, tier, placeholder, queries[{promql, legend}], findings, trace, original}]}`; for monitors `{kind, source_ref, title, artifact, tier, enabled, promql, thresholds, findings, trace, original}`. Inputs that couldn't be processed at all go in `summary.skipped` and in the log as `input.skipped`.

`plan` refreshes an existing plan folder: if `plan.json` is already there, stale `perses/*.json` files are removed first. It never deletes anything in a folder that doesn't contain a `plan.json`.

## `apply`

1. **Load** (`apply.py`): read `plan.json`; refuse a plan with another `version` (currently 2), or whose recorded validation has structure or `promtool` errors; read every dashboard artifact and `check-rules.json`; compare each file's SHA-256 with the one `plan` recorded, ignoring CRLF vs LF (git's `autocrlf` rewrites line endings, which is not an edit). A real mismatch means it was edited by hand: warn, import the edited file. **Completeness check:** every monitor that `plan.json` marks importable must be present in `check-rules.json`, with a matching count, or `apply` refuses. A first version of `apply` read an optional entry and silently skipped six alerts while reporting success; that is now a tested failure.
2. **Re-validate** the files as they are now (dashboard structure, check-rule schema) and check Dash0's size limits (256 KB per dashboard, 1000 rules and 2 MB for the rule batch). A broken edit is refused before anything is sent.
3. **Show** the target and every item; **confirm** by typing `yes` (`--auto-approve` skips, `--dry-run` stops here). Anything other than `yes`, including no input at all, cancels with exit code 1.
4. **Import:** one `POST /api/import/dashboard` per dashboard, then one `POST /api/import/check-rules` with all rules. Failures are collected, not fatal: a rejected dashboard doesn't stop the rules, and a rejected rule batch says that nothing from it was written.
5. **Record** the outcome in `apply.json` (dashboard ids, statuses, the `created` count for rules; never credentials). Exit 1 if anything failed.

`DASH0_API_URL` must be `https://` (or `localhost` for tests), so the token cannot go out in plain text. `DASH0_DATASET` is checked against Dash0's slug pattern before any request.

## `explain`

Answers "why does this panel look like that?" without re-translating anything.

- `explain --find TEXT [--plan DIR]` searches a plan folder's `plan.json` for panels (title or key such as `panel-19`) and monitors (title) and prints each one's Datadog source, every rule that ran in order with its tier and before/after, the generated PromQL, the thresholds, and the findings. More than 8 matches asks you to narrow down; no match lists what exists.
- `explain --query TEXT [--var NAME=TAGPREFIX ...] [--monitor]` translates a single query with the bundled mappings and shows the same. `--var host=host` declares a template variable with tag prefix `host`; `--var scope=` declares a free-form one. An undeclared `$var` is reported as `dd.var.unknown`, not guessed. `--monitor` treats the text as a monitor query.
- `--json` prints `{"explanations": [...]}` for tools. The logic is in `explain.py` and returns plain dicts, so a backend can reuse it.

The trace it prints is the one recorded during `plan` (the `Recorder` steps), so it cannot disagree with what was generated.

## Dash0 integration (from the documentation; one empty dashboard import verified)

| Endpoint | Notes |
|---|---|
| `POST /api/import/dashboard` | Body `{kind: "Dashboard", metadata, spec}`. `metadata.dash0Extensions.id` set → overwrite; not set or unknown → create. Bearer auth, `?dataset=`. |
| `POST /api/import/check-rules` | `{items: [...]}`, at most 1000, **atomic** (one invalid item rejects the whole batch), 2 MB body. Rule fields: `id, name, expression, thresholds{degraded, failed}, interval, for, keep_firing_for, labels, annotations, enabled`. |

Configuration: `DASH0_AUTH_TOKEN`, `DASH0_API_URL` (default `https://api.eu-west-1.aws.dash0.com`; must match your org's region), `DASH0_DATASET` (default `default`). Datadog: `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE` (default `datadoghq.eu`).

These are stored by `openborder init` / `config set` in `cli/.env` (gitignored; override the location with `OPENBORDER_ENV_FILE`). `config.load_settings()` resolves each value as real environment variable, then file, then default. The clients receive these as plain arguments from the CLI shell; they never read the environment themselves. The `.env` parser is about 30 lines of standard library, so we don't depend on `python-dotenv`.

## Datadog API notes (observed)

- **Base URL:** `https://api.<site>`; the site is normalized, so `datadoghq.eu`, `app.datadoghq.eu` and a full URL all work.
- **Auth:** `DD-API-KEY` + `DD-APPLICATION-KEY` headers. Verified against a real EU org: this works with an API key plus either an application key or a `ddpat_` Personal Access Token in the second slot. The `ddpat_` token alone also worked as `Authorization: Bearer`; we don't use that mode.
- **Dashboards:** `GET /api/v1/dashboard?count=100&start=N` returns summaries only (`id`, `title`, `layout_type`, `modified_at`, and so on). It lists only custom or cloned dashboards, never Datadog's built-in ones. `paginate()` follows `start` until a short page and stops if a page adds nothing new. `GET /api/v1/dashboard/{id}` returns the full definition; `plan` uses it, and it has been verified against a real org.
- **Monitors:** `GET /api/v1/monitor`; with no `page` parameter Datadog returns every match in one response. `--tag` / `--monitor-tag` maps to `monitor_tags`. The response has the same shape as an exported monitor, so one converter handles both.
- **Errors:** HTTP 401/403 and 429 get specific messages (`explain_http_error`); network failures become a `DatadogError`. The CLI never prints credentials.

## Building on top of the CLI: backend and GUI

Not built. The idea is the way AWS offers a CLI and a console: a GUI for less technical users on the same engine. A `backend/` and a `frontend/` folder are reserved for it. Two ways to build the backend; the first is what is planned, the second is the better end state.

### 1. Call the CLI as a subprocess (what works today)

Everything below is how the CLI really behaves now.

| Topic | Contract |
|---|---|
| How to run | `python -m openborder <command>` using the venv in `cli/`, **with the working directory set to `cli/`** (or `PYTHONPATH` pointing at it), because the package is not pip-installed |
| Credentials | Environment variables win over `cli/.env`: `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE`, `DASH0_AUTH_TOKEN`, `DASH0_API_URL`, `DASH0_DATASET`. A multi-user backend should set them **per call** and never run `init`/`config set`. Set `OPENBORDER_ENV_FILE` to a path that does not exist so a shared `cli/.env` is never read. Never log the environment |
| Exit codes | `0` success · `1` failure (a one-line explanation is printed) · `2` usage error. `plan` exits `1` if validation failed, but still writes the artifacts |
| Machine-readable results | `plan` writes `<out>/plan.json`, `check-rules.json`, `perses/*.json`, `migration.log`; `apply` writes `<dir>/apply.json`; `discover --json` and `explain --json` print JSON on stdout. **Read the files, do not parse the Rich text.** Errors are plain text on stdout with a non-zero exit code, so check the exit code before parsing |
| Prompts | `apply` asks for `yes` unless `--auto-approve`. A GUI should build its own confirmation screen from `plan.json` and then call `apply --auto-approve`. Never call `init` (interactive) |
| Isolation | Use a fresh `--out` folder per job. `plan` deletes stale `perses/*.json` when the folder already has a `plan.json` |
| `apply` results | `apply.json` is written once the import stage starts; failures before that (missing token, a plan that failed validation or has another `version`) exit `1` with text only |
| Docker | `plan` runs `promtool` through Docker; pass `--no-promtool` on a host without it |
| Output | Set `NO_COLOR=1`. The entry point forces UTF-8 so tier symbols cannot crash a legacy Windows console |
| Time | `plan` fetches dashboards one at a time and `apply` imports one request each; there is no progress stream, so run them as background jobs and poll |

Useful files for a UI: `plan.json` has, per dashboard and panel, the tier, the findings, the rule trace, the generated PromQL and the original Datadog widget, and `summary` has the counts; that is enough to draw the "before → after" screens without calling the CLI again. `explain --find TEXT --json` returns one panel's trace.

### 2. Import it as a library (recommended once the backend exists)

The pure engine is reusable as is: `openborder.core.plan()`, `core.render_plan_json()`, `explain.find_in_plan()` / `explain_query()`, `apply.load_plan()`, `DatadogClient`, `Dash0Client`. What still lives in `cli.py` and would have to be copied or extracted: input selection and filtering (`--dashboard`, `--name`, `--monitor-tag`), the `promtool` call, writing the artifacts, and the import loop with its per-item results.

Suggested first step for the backend work: move those four pieces into one module that returns data and never prints or raises `typer.Exit` (for example `openborder/service.py` with `run_plan(...)`, `run_apply(...)`, `run_discover(...)`), make the CLI commands thin callers of it, and have the backend call the same functions. The 214 tests cover the commands, so the refactor is safe to do first.

### Not solved yet (a hosted GUI will need these)

- Credentials of other people: the CLI is single-tenant. Keys must be request-scoped and never persisted or logged.
- Progress reporting and cancellation for long `plan` / `apply` runs.
- Authentication and authorisation of the backend itself, and rate limiting (it can write to a customer's Dash0 org).
- A pre-import confirmation that is built from `plan.json` rather than from terminal text.
- Hosting: nothing is decided. Supabase Edge Functions run only JavaScript/TypeScript, so a Python engine needs a Python host (a container, or Vercel's Python runtime).

## Adding a vendor (later)

Add `sources/<vendor>/` that produces the same IR, plus `mappings/<vendor>/*.yaml`. The targets, validation, CLI, and report need no changes. We will not build an adapter interface until a second vendor actually exists.
