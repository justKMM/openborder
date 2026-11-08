# Compatibility matrix

Each row is one translation rule. A rule's **tier is fixed in code**, and the `rule_id` column matches the `rule_id` in findings, in the `plan.json` trace, and in the tests. `cli/tests/test_lower.py` has one row per query rule with the exact PromQL it must produce.

**Tiers:** ✓ exact · ≈ equivalent (a known, bounded difference that doesn't change which series show up or whether an alert fires in practice) · ~ approximate (can change which series appear or when an alert fires; alerts are imported **disabled**) · ✗ manual (no equivalent: placeholder panel, or the alert is not imported).

A panel or alert takes the worst tier of its rules. A rule recorded as "✓" never produces a finding.

**Status:** ☑ implemented and unit-tested · ☐ planned (today it falls back to the ✗ row below it) · 🧪 additionally verified in a live Dash0 import (nothing is verified live yet).

## Metric queries

| Datadog | PromQL (Dash0) | Tier | rule_id | Status |
|---|---|---|---|---|
| metric with a mapping entry | `{otel_metric_name="<mapped>"}` plus fixed matchers (e.g. `state="sys"`) | ✓ | `mapping.metric` | ☑ |
| metric with no mapping entry | `{otel_metric_name="<datadog name>"}`; will probably show no data | ~ | `mapping.unmapped-metric` | ☑ |
| Datadog per-second rate of a cumulative counter | `rate(x[$__rate_interval])` (`[<window>]` in monitors) | ≈ | `mapping.transform.rate` | ☑ |
| raw running total (Datadog `GAUGE_KEYS`) | the OTel cumulative sum as is, **no `rate()`** | ✓ | `mapping.metric` | ☑ |
| tag with a mapping (`host`) | mapped label (`host_name`) | ✓ | `mapping.tag` | ☑ |
| tag with no mapping | tag with non-identifier characters replaced by `_`; may not exist on the series | ~ | `mapping.unmapped-tag` | ☑ |
| `{*}` | no extra matchers | ✓ | (no rule) | ☑ |
| `{k:v}`, `{!k:v}` | `k="v"`, `k!="v"` | ✓ | `dd.filter.eq` | ☑ |
| `{k:web-*}` | `k=~"^web-.*$"` (regex characters escaped, anchored) | ✓ | `dd.filter.wildcard` | ☑ |
| `{k IN (a,b)}`, `{k NOT IN (a,b)}` | `k=~"^(a\|b)$"`, `k!~"^(a\|b)$"` | ✓ | `dd.filter.in` | ☑ |
| filter value with uppercase | exact match kept; Datadog lowercases tag values, OTel keeps case | ≈ | `dd.filter.case` | ☑ |
| `OR`/`AND` filter, bare tag | cannot be expressed as label matchers | ✗ | `dd.filter.unsupported` | ☑ |
| `{$var}` or `{k:$var}` where the variable has a tag prefix | `label=~"^($var)$"` (Dash0 interpolates the selected values, `a\|b\|c` for multi-select) | ✓ | `dd.var.prefixed` | ☑ |
| `{$scope}`-style free-form variable, default `*` | filter dropped; identical under the default (reported once per dashboard, see below) | ✓ | `dd.var.free-scope` | ☑ |
| free-form variable with a non-`*` default | filter dropped, so the panel shows more series | ~ | `dd.var.free-scope` | ☑ |
| `$var` not defined on the dashboard | — | ✗ | `dd.var.unknown` | ☑ |
| `avg:` `sum:` `min:` `max:` | `avg()` `sum()` `min()` `max()` | ✓ | `dd.space-agg` | ☑ |
| `p50:`…`p99:` and other space aggregators | — | ✗ | `dd.space-agg.unsupported` | ☑ |
| `by {a,b}` | `by (a', b')` with mapped labels | ✓ | `mapping.tag` | ☑ |
| `.rollup(avg\|min\|max\|sum\|count, N)` on a gauge | `*_over_time(x[Ns])` | ✓ | `dd.rollup` | ☑ |
| `.rollup(agg)` without an interval | `*_over_time(x[$__interval])` | ≈ | `dd.rollup.auto` | ☑ |
| `.rollup()` on a rate series | `*_over_time(rate(x[w])[Ns:])` subquery (resamples the rate) | ~ | `dd.rollup.on-rate` | ☑ |
| `.rollup(<other aggregator>)` | — | ✗ | `dd.rollup.unsupported-agg` | ☑ |
| `.as_rate()` on a rate metric | no-op (already a rate) | ≈ | `dd.as-rate` | ☑ |
| `.as_rate()` on a gauge | `rate(x[w])`; meaning may differ | ~ | `dd.as-rate` | ☑ |
| `.as_count()` on a rate metric | `increase(x[$__interval])` (extrapolates; not an integer) | ≈ | `dd.as-count` | ☑ |
| `.as_count()` on a gauge | ignored | ~ | `dd.as-count` | ☑ |
| `.fill(null)` | PromQL default | ✓ | `dd.fill.null` | ☑ |
| `.fill(zero\|last\|linear\|…)` | ignored; gaps stay | ~ | `dd.fill.other` | ☑ |
| any other `.method()` | — | ✗ | `dd.method.unsupported` | ☑ |
| arithmetic with a scalar | same operator | ✓ | `dd.arith.scalar` | ☑ |
| arithmetic between queries with identical grouping | binary operator | ✓ | `dd.arith.vector` | ☑ |
| arithmetic between queries with different grouping | `on(<common labels>) group_left\|group_right` | ~ | `dd.arith.mismatched` | ☑ |
| `queryN` reference to a query that failed or is missing | — | ✗ | `dd.formula.ref-unavailable` | ☑ |
| `abs`, `log2`, `log10`, `sqrt`, `exp` | same function | ✓ | `dd.fn.math` | ☑ |
| `per_second/minute/hour(x)` where `x` is already a rate | `x`, `60 * x`, `3600 * x` | ✓ | `dd.fn.per-unit` | ☑ |
| `per_second/minute/hour(x)` on a raw total or gauge | `rate()` on its selectors, then scaled; Datadog's exact normalisation isn't reproduced | ~ | `dd.fn.per-unit.gauge` | ☑ |
| `diff(x)` | `x - (x offset $__interval)` | ~ | `dd.fn.diff` | ☑ |
| `top(x, N, 'mean'\|'avg'\|'max'\|'min'\|'sum'\|'last', 'desc'\|'asc')` | `topk\|bottomk(N, <agg>_over_time((x)[$__range:]))`; Datadog ranks once over the range, PromQL re-ranks per step | ~ | `dd.fn.top` | ☑ |
| `top()` with other aggregators, in monitors, or non-literal arguments | — | ✗ | `dd.fn.top` | ☑ |
| wrong argument count | — | ✗ | `dd.fn.arity` | ☑ |
| any other function (`anomalies`, `forecast`, `outliers`, `cumsum`, `ewma_*`, `clamp_*`, …) | — | ✗ | `dd.fn.unsupported` | ☑ |
| unparseable query | — | ✗ | `dd.query.parse-error` | ☑ |
| log / APM / RUM / event / process / SLO data sources | — | ✗ | `dd.datasource.unsupported` | ☑ |
| `week_before()` / `timeshift()` → `offset` | `offset 1w` etc. | ✓ | — | ☐ |
| `derivative(x)`, `clamp_min/max`, `count_nonzero` | `deriv()`, `clamp_*`, … | ~/✓ | — | ☐ |

## Widgets

"In Redis - Overview" is the number of such widgets in the real `redisdb` dashboard.

| Datadog widget | Perses (Dash0) | Tier | rule_id | Status | In Redis - Overview |
|---|---|---|---|---|---|
| `timeseries` (line / area / bars) | `TimeSeriesChart` (display, legend on/off, y-axis min 0, unit) | ✓ | `dd.widget.timeseries` | ☑ | 15 |
| `timeseries` or `query_value` with markers or conditional formats | as above; marker lines and conditional colours are not migrated and are listed | ≈ | `dd.widget.styling-dropped` | ☑ | 10 (3 + 7) |
| `query_value`, one query | `StatChart` with the matching calculation (`avg`→`mean`, `max`, `min`, `last`, `sum`) | ≈ | `dd.widget.query-value` | ☑ | 8 |
| `query_value`, formula over several queries | `StatChart`; Datadog aggregates each query over the range *then* applies the formula (ratio of averages), the panel averages the per-step ratio | ~ | `dd.widget.query-value.formula` | ☑ | 2 |
| `query_value` whose queries use different aggregators | first aggregator applied to the whole value | ~ | `dd.widget.query-value.mixed-aggregators` | ☑ | 0 |
| `note` | `Markdown` (colours and fonts are dropped) | ✓ | `dd.widget.note` | ☑ | 7 |
| `group` | titled grid section (title hidden when `show_title` is false) | ✓ | (structure) | ☑ | 7 |
| `toplist` | placeholder: Dash0's documented panel kinds include no ranked bar or table | ✗ | `dd.widget.toplist` | ☑ | 1 |
| `list_stream`, `log_stream`, `event_stream` | placeholder | ✗ | `dd.widget.stream` | ☑ | 2 |
| `distribution`, `heatmap` | placeholder | ✗ | `dd.widget.distribution` | ☑ | 1 |
| any other widget type | placeholder | ✗ | `dd.widget.unsupported` | ☑ | 0 |
| widget with no readable requests | placeholder | ✗ | `dd.widget.shape` | ☑ | 0 |
| unexpected translator error | placeholder (the rest of the dashboard still translates) | ✗ | `internal-error` | ☑ | 0 |
| `query_table`, `change`, `alert_graph`, `alert_value`, `image`, `iframe` | `TimeSeriesTable` / `StatChart` / `Markdown` | ≈/~ | — | ☐ | 0 |

A placeholder is a `Markdown` panel in the original grid position. It states the reason and quotes the original queries, and the full original widget is kept in `plan.json`.

**Layout.**
- `ordered`: each group's inner grid spans the group's own width `W` (1–12), so inner coordinates are scaled by `24/W` into the Perses 24-column grid. Datadog row heights are multiplied by 3 (`HEIGHT_SCALE`, to be calibrated against Dash0's rendering).
- Groups that Datadog placed side by side are stacked vertically: ≈, reported once per dashboard as `dd.layout.groups-stacked`.
- `free` layout is snapped to the grid: ≈, `dd.layout.free`.
- Always dropped (cosmetic, never reported): colours, palettes, line styles, fonts, text alignment, legend columns.

**Template variables.** A variable with a tag prefix (`host`) becomes a Perses `ListVariable` over the mapped label, "All" as `.*`. A free-form variable (`scope`, no prefix) cannot map to a label: it is not created, queries drop its filter, and the loss of *filtering by arbitrary tags* is reported once per dashboard (≈, `dd.var.free-scope`) instead of on every panel, because results under the default value are identical.

## Monitors

| Datadog | Dash0 check rule | Tier | rule_id | Status |
|---|---|---|---|---|
| `query alert` / `metric alert` | rule with a PromQL `expression` | per query | — | ☑ |
| `avg\|max\|min\|sum(last_N<unit>)` over one aggregated selector with the same aggregator | `avg(avg_over_time(x[Nm]))` | ✓ | `dd.monitor.window.pushdown` | ☑ |
| any other window | `*_over_time((query)[Nm:1m])` subquery | ≈ | `dd.monitor.window.subquery` | ☑ |
| comparator `>` `>=` | `… > $__threshold` | ✓ | `dd.monitor.comparator` | ☑ |
| comparator `<` `<=` | `… < $__threshold`; Dash0 maps the higher threshold to `failed`, so severities may be reversed (verify) | ~ | `dd.monitor.comparator.below` | ☑ |
| comparator `==` `!=` | — | ✗ | `dd.monitor.comparator` | ☑ |
| `thresholds.critical` / `.warning` | `thresholds.failed` / `.degraded` | ✓ | `dd.monitor.thresholds` | ☑ |
| query threshold ≠ `thresholds.critical` | `thresholds.critical` used | ~ | `dd.monitor.thresholds` | ☑ |
| `name` without templates | `name` and `annotations.summary` | ✓ | — | ☑ |
| `name` with `{{…}}` templates (`… for host {{host.name}}`) | static `name` (the template phrase removed, e.g. `Disk latency is high`); the full title with its templates is kept in `annotations.summary`, because Dash0 does not expand templates in a check's name | ≈ | `dd.monitor.name` | ☑ |
| `message` with `{{value}}`, `{{host.name}}`, `{{#is_alert}}`, `{{#is_warning}}` | `annotations.description` in Prometheus template syntax; severity blocks flattened (both texts appear); recovery/no-data blocks removed; other variables left and listed | ≈ | `dd.monitor.message` | ☑ |
| `@`-mentions | removed and listed; routing must be set up in Dash0 | ≈ | `dd.monitor.notify` | ☑ |
| `tags` `k:v` | `labels` `{k: v}`; bare tags become `"true"`; `priority` → `labels.priority` | ✓ | — | ☑ |
| `renotify_interval`, `escalation_message`, `evaluation_delay`, `require_full_window`, `new_host_delay`, `new_group_delay` | not migrated (listed when set) | ≈ | `dd.monitor.options-ignored` | ☑ |
| `notify_no_data: true` | not migrated: the rule stays silent when the series disappears | ~ | `dd.monitor.no-data` | ☑ |
| evaluation cadence | `interval: 1m` | ≈ | — | ☑ |
| `service check`, `log alert`, `trace-analytics`, `composite`, `event`, `synthetics`, `slo`, `anomaly`/`forecast`/`outlier` | not imported | ✗ | `dd.monitor.unsupported` | ☑ |
| `forecast()`, `anomalies()`, `outliers()`, `change()`, `pct_change()` and other query shapes | not imported; the reason names what the monitor is (e.g. "forecast monitors have no Prometheus equivalent") | ✗ | `dd.monitor.unsupported-query` | ☑ |
| companion `absent_over_time` rule for no-data | — | ~ | — | ☐ |

Every alert also carries `annotations.openborder_tier`, `openborder_source` (`datadog:monitor:<id>`) and `openborder_original_query`.

## Redis metric mapping (Datadog → OTel `redisreceiver`)

The source of truth is [`cli/openborder/mappings/datadog/redis.yaml`](../cli/openborder/mappings/datadog/redis.yaml), which cites its evidence. Two sources were read for it:
- **Datadog:** `redisdb/datadog_checks/redisdb/constants.py` says whether the agent submits a value raw (`GAUGE_KEYS`) or as a per-second rate (`RATE_KEYS`). **`metadata.csv` cannot be used for this:** it labels 270 of 271 Redis metrics `gauge`, rates included.
- **OTel:** the `redisreceiver` documentation.

| Datadog metric | Datadog submits | OTel metric | Transform |
|---|---|---|---|
| `redis.mem.used`, `.rss`, `.fragmentation_ratio` | raw | `redis.memory.used`, `.rss`, `.fragmentation_ratio` | none |
| `redis.mem.maxmemory` | raw | `redis.maxmemory` (**optional metric, disabled by default**: enable it in the collector) | none |
| `redis.net.clients`, `redis.clients.blocked`, `redis.net.slaves` | raw | `redis.clients.connected`, `redis.clients.blocked`, `redis.slaves.connected` | none |
| `redis.rdb.changes_since_last` | raw | `redis.rdb.changes_since_last_save` | none |
| `redis.keys`, `redis.expires` | raw, tag `redis_db` | `redis.db.keys`, `redis.db.expires` (attribute `db`) | none |
| `redis.keys.evicted`, `.expired`, `redis.net.rejected` | **raw running total** | `redis.keys.evicted`, `redis.keys.expired`, `redis.connections.rejected` | none (no `rate()`) |
| `redis.stats.keyspace_hits`, `.keyspace_misses` | **rate** | `redis.keyspace.hits`, `.misses` | `rate()` |
| `redis.net.commands` | **rate** of `total_commands_processed` | `redis.commands.processed` | `rate()` |
| `redis.cpu.sys`, `redis.cpu.user` | **rate** of `used_cpu_*` | `redis.cpu.time{state="sys"\|"user"}` | `rate()` |
| `redis.info.latency_ms`, `redis.slowlog.micros.*`, `redis.replication.delay`, `redis.replication.master_link_down_since_seconds`, `redis.key.length` | — | none in `redisreceiver` | unmapped (~) |

**Tags:** `host` → `host_name` (assumes the collector adds `host.name`, e.g. with the `resourcedetection` processor); `redis_host` → `server_address` and `redis_port` → `server_port` (resource attributes disabled by default); `redis_db` → `db`, **but the values differ**: Datadog's is `db0`, the collector's is `0`, so a filter on a specific database won't match (the Redis dashboard has none). Other tags pass through with a ~ finding.

**Verified against a real collector (2026-11-08):** every OTel metric above was emitted by `redisreceiver` in OpenTelemetry Collector 0.165.0 against `redis:7-alpine` (with `redis.maxmemory` enabled), with the attributes and types the mapping assumes: `rate()` is only used on monotonic cumulative sums, and `state=sys|user` occurs on `redis.cpu.time`. The captured output is `cli/tests/fixtures/otel/redisreceiver.json` and `cli/tests/test_mapping_vs_collector.py` fails if the mapping drifts from it. The unmapped Datadog metrics are not emitted by the receiver.

## Known semantic differences (detail)

- **Datadog's rollup vs. PromQL's lookback:** Datadog averages raw points into display buckets; PromQL takes the latest sample in a lookback window at each step. Treated as ✓ for gauges.
- **Per-second rates:** the Datadog agent computes a rate between two check runs; PromQL `rate()` averages over `$__rate_interval` and extrapolates. Spikes shorter than the window are smoothed (≈).
- **`query_value` with several queries:** ratio of the range-averages (Datadog) vs. average of the per-step ratios (Dash0). Close for steady series, different for bursty ones (~).
- **`top()`:** Datadog ranks once over the whole range; `topk(…avg_over_time(…[$__range:]))` re-ranks at each step (~).
- **Window aggregation in monitors:** `avg(last_5m)` over a formula is evaluated as a subquery at 1-minute resolution (≈).
- **Tag value case:** Datadog lowercases tag values, OTel attribute values keep their case (≈, `dd.filter.case`).
- **Message templates:** Dash0's alert template syntax is assumed to match Prometheus (`{{ $value }}`, `{{ $labels.x }}`); not yet verified.

## What Dash0's reference pages say (read 2026-11-08, shaped the output)

Source: the [TimeSeriesChart](https://www.dash0.com/docs/dash0/dashboards/types/TimeSeriesChart) and [StatChart](https://www.dash0.com/docs/dash0/dashboards/types/StatChart) panel pages and the [list variables](https://www.dash0.com/docs/dash0/dashboards/add-variables/use-list-variables) page.

- **Regex matchers are unanchored in Dash0** (`=~` is a search, unlike Prometheus). Every regex we emit is therefore anchored explicitly (`^(…)$`), which means the same thing under both engines.
- **List variables** are referenced as `$name` inside a label matcher; the query supplies the label and operator, Dash0 supplies only the values.
- **Format objects** (`yAxis.format`, StatChart `format`) always need a `unit`; `decimalPlaces` alone is invalid, so a panel with a precision but no unit gets `"unit": "decimal"`. All unit names we emit (`percent`, `seconds`, `milliseconds`, `bytes`, `decimal`) are in the documented list.
- `StatChart.calculation` accepts `first`, `last`, `first-number`, `last-number`, `mean`, `sum`, `min`, `max`, `instant`; we use `mean`, `max`, `min`, `last`, `sum`.
- TimeSeriesChart legend uses `showLegend` and `position`.
- **Not documented anywhere we found:** the JSON shape of a variable definition (variable kind and plugin names). The `ListVariable` / `PrometheusLabelValuesVariable` shape is our assumption from upstream Perses; a real import will show.

## Verified against a live Dash0 (2026-11-08)

- `POST /api/import/dashboard` accepted a dashboard with no panels (an empty Datadog dashboard) in an EU org (`https://api.europe-west4.gcp.dash0.com`, dataset `default`): authentication, endpoint, request envelope (`kind`, `metadata.dash0Extensions.id`, `spec`) and response parsing work.

## Not verified against a live Dash0 yet

That import had no panels, no variables and no layouts, so none of the panel dialect has been exercised. These are generated from Dash0's documentation but have not been imported: the `ListVariable` / `PrometheusLabelValuesVariable` spec, `StatChart` and `TimeSeriesChart` option names, whether `$__rate_interval`, `$__interval` and `$__range` are substituted in Dash0, how multi-select variables expand inside `=~"$host"`, `thresholds.degraded`/`failed` ordering for below-threshold rules, and `metadata.dash0Extensions.id` overwrite behaviour. `apply` is the step that will verify them.
