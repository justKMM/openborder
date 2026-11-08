# OpenBorder

> **`terraform plan` for leaving your observability vendor.**

OpenBorder migrates Datadog dashboards and monitors to [Dash0](https://www.dash0.com), using open standards. Dashboards become [Perses](https://perses.dev), queries become PromQL, and alerts become Prometheus alerting rules with Dash0's `$__threshold` extension. Metric names follow OpenTelemetry semantic conventions.

The translation path contains **no LLMs**. Every query passes through a parser and named translation rules, and each rule has a fixed fidelity tier. That makes every translation deterministic, testable, and explainable.

> 🚧 **Status:** hackathon build in progress (thinc! 2026, Dash0 challenge). The commands below describe the target interface. Every command works: `init`, `config`, `discover`, `plan`, `apply`, `explain`. `apply` has imported an empty dashboard into a real Dash0 org, but **no generated panels or alert rules have been imported there yet**, so how Dash0 renders them is still unverified. See [docs/SCOPE.md](docs/SCOPE.md) for what is in and out.

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

python -m openborder init                                   # prompts for credentials, saves them to cli/.env (gitignored)

python -m openborder discover                                # list dashboards & monitors (read-only); filter with --name redis, --tag integration:redisdb
python -m openborder plan --dashboard abc-def-ghi --monitor-tag integration:redisdb   # translate and write ./migration (imports nothing)
python -m openborder plan --from-dir tests/fixtures/datadog                          # or work offline from exported JSON
python -m openborder explain --find evictions               # how one panel or monitor of the plan was translated, rule by rule
python -m openborder explain --query 'avg:redis.stats.keyspace_hits{*}'   # or trace any single query
python -m openborder apply ./migration                      # shows what it will send, you type 'yes'; --dry-run sends nothing, --auto-approve skips the prompt
```

**Configuration.** `init` asks for these six values once and stores them in `cli/.env`, so you don't need to `export` them in every new terminal. Run `init` again to update them (Enter keeps the current value), or change a single one with `python -m openborder config set DD_SITE datadoghq.com`. `config show` lists the current values with secrets masked. Real environment variables take precedence over the file.

| Key | Default | Where to get it |
|---|---|---|
| `DD_API_KEY` | none | Datadog: Organization Settings > API Keys |
| `DD_APP_KEY` | none | Datadog: Organization Settings > Application Keys |
| `DD_SITE` | `datadoghq.eu` | your Datadog site |
| `DASH0_AUTH_TOKEN` | none | Dash0: Settings > Auth Tokens |
| `DASH0_API_URL` | `https://api.eu-west-1.aws.dash0.com` | must match your Dash0 org's region |
| `DASH0_DATASET` | `default` | Dash0 dataset slug |

**Datadog credentials.** `DD_API_KEY` and `DD_APP_KEY` are sent as the `DD-API-KEY` and `DD-APPLICATION-KEY` headers, so the first must be an *API key* and the second an *application key*. A Datadog Personal Access Token (`ddpat_…`) is accepted in the application-key slot; it was observed to work on the EU site, but isn't something we've tested beyond that. If `discover` reports HTTP 401, the usual cause is the two values being swapped, or a key from a different Datadog site than `DD_SITE`.

**What `discover` can see.** Datadog's API lists only *custom or cloned* dashboards, not its built-in ones. To try the Redis demo content, import `redisdb/assets/dashboards/overview.json` and `redisdb/assets/monitors/high_mem.json` from [integrations-core](https://github.com/DataDog/integrations-core) into your Datadog org first.

To work offline, use `--from-dir PATH` instead of the Datadog API. It reads exported dashboard and monitor JSON, for example from [DataDog/integrations-core](https://github.com/DataDog/integrations-core).

### `plan`

`plan` translates everything and writes the result to `./migration` (change it with `--out`); it never imports anything. Selection: `--dashboard ID` (repeatable) and `--monitor-tag TAG` pick specific items, `--name TEXT` filters both by name, `--no-dashboards` / `--no-monitors` skip a kind. Other flags: `--enable-approximate` (arm ~ alerts instead of importing them disabled) and `--no-promtool` (skip the Docker-based PromQL syntax check).

What it writes: `perses/<dashboard>.json` and `check-rules.json` (the exact bodies Dash0's import APIs take), `plan.json` (everything needed to review it, including each panel's rule trace and the original Datadog definition), and `migration.log` (one JSON line per finding). The same input always produces byte-identical files.

### Example plan output (real run on the Redis assets from integrations-core, abridged)

```
Redis - Overview  →  Perses dashboard
├── Performance Metrics
│   ├── ~ Latency by Host  'redis.info.latency_ms' has no known OpenTelemetry equivalent; it is queried …
│   ├── ✗ Slowlog query rates  Dash0 has no ranked bar/table panel we can target yet; recreate it as …
│   ├── ≈ Average CPU usage  Datadog's agent reports a per-second rate computed between check runs; …
│   └── ~ Cache hit rate  Datadog aggregates each query over the time range first and then evaluates …
├── Memory Metrics
│   ├── ≈ Percent Used Memory by Host  not migrated: 2 marker line(s)
│   ├── ~ Evictions  per_minute() on a series that is not already a rate was translated to a per-secon…
│   └── ✓ Total allocated memory  sum({otel_metric_name="redis.memory.rss", host_name=~"^($host)$"})
…
  Coverage: 32/36 panels migrated (89%)  ✓ 13 · ≈ 11 · ~ 8 · ✗ 4

Monitors  →  Dash0 check rules
└── ≈ [Redis] High memory consumption  degraded 70 · failed 90  enabled
  Armed: 1/1

✓ promtool accepted all 24 PromQL expressions
Artifacts written to migration  ·  review them, then run `python -m openborder apply migration`
```

The `✗` panels are not lost: each becomes a placeholder panel in the same grid position that explains why and quotes the original query.

### `apply`

`apply [FOLDER]` (default `./migration`) imports what `plan` wrote, and nothing else: it never re-translates. It re-reads and re-validates the files, shows the target (`DASH0_API_URL`, dataset), the dashboards and the check rules, and asks you to type `yes`. `--dry-run` shows the same and sends nothing; `--auto-approve` skips the question.

- A file you edited by hand after `plan` is imported (with a warning), unless the edit broke it, in which case nothing is sent.
- A plan that failed validation is refused.
- Dashboards are imported one request each, so one failure does not block the others. Check rules go in one atomic request: if Dash0 rejects any rule, none of them is written, and the message says so.
- Every dashboard and rule has a stable ID, so running `apply` again is meant to overwrite rather than duplicate. That follows Dash0's documentation and hasn't been confirmed against a real org yet.
- The token is sent only over `https://` (plain `http://` is accepted for `localhost` only).
- Results, without any credentials, are written to `apply.json` in the plan folder.

```
$ python -m openborder apply ./migration
Target  https://api.eu-west-1.aws.dash0.com  ·  dataset 'default'

Dashboards (1)
└── Redis - Overview  32/36 panels migrated, 4 placeholder(s) for widgets with no equivalent
Check rules (1), one atomic request
└── ≈ [Redis] High memory consumption  degraded 70 · failed 90  enabled

Dash0 will create these items, or overwrite them if they were imported before.
Type 'yes' to import:
```

### `explain`

`explain` shows why a translation looks the way it does: the Datadog source, every rule that ran in order (with its tier and what it turned the input into), the resulting PromQL, and the findings. Every step is a named rule from [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md); nothing is guessed. `--find TEXT` looks up a panel (by title or key like `panel-19`) or monitor in the plan folder; `--query TEXT` traces a raw query without any plan (`--var host=host` declares template variables, `--monitor` treats it as a monitor query).

### Machine-readable output

`plan` and `apply` write `plan.json` and `apply.json`; `discover --json` and `explain --json` print JSON. Everything else is for people. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#building-on-top-of-the-cli-backend-and-gui) if you want to build on the CLI.

## Live-data demo

[`demo/`](demo/) is a `docker-compose` setup with Redis (small `maxmemory`, so evictions happen), a load generator, and an OpenTelemetry Collector (`redisreceiver`) that sends metrics to Dash0, so the migrated dashboard can show real data instead of an empty layout. It starts and the collector's output has been checked against our metric mapping; sending to Dash0 needs your endpoint and token and has not been run. See [demo/README.md](demo/README.md).

## Documentation

- [docs/SCOPE.md](docs/SCOPE.md): scope, non-goals, priorities, and timeline
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): pipeline, IR, fidelity model, and Dash0 integration
- [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md): what translates, how, and at which tier
- [demo/README.md](demo/README.md): the live Redis data stack
