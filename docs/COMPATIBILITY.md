# Compatibility matrix

Each row is one translation rule. The rule's **tier is fixed in code**, and the `rule_id` column matches the `rule_id` in findings, in `openborder explain` traces, and in the golden test names.

**Tiers:** ✓ exact · ≈ equivalent (bounded difference, no practical change in which series appear or whether an alert fires) · ~ approximate (may change which series appear or when an alert fires; alerts are imported disabled) · ✗ manual (no equivalent; placeholder or not imported).

**Priority:** P0 must-have · P1 stretch · — out of scope (reported as ✗).
**Status:** ☐ planned · ☑ implemented · 🧪 verified in Dash0.

## Metric queries

| Datadog | PromQL (Dash0) | Tier | rule_id | Prio | Status |
|---|---|---|---|---|---|
| metric name | `{otel_metric_name="<mapped>"}` via the mapping file | ✓ | `mapping.metric` | P0 | ☐ |
| metric with no mapping entry | `{otel_metric_name="<datadog name>"}` | ~ | `mapping.unmapped-metric` | P0 | ☐ |
| per-second gauge ↔ OTel counter | `rate(x[$__rate_interval])` | ≈ | `mapping.transform.rate` | P0 | ☐ |
| `{*}` | no extra matchers | ✓ | `dd.filter.all` | P0 | ☐ |
| `{k:v}`, `{!k:v}` | `k="v"`, `k!="v"` (key mapped) | ✓ | `dd.filter.eq` | P0 | ☐ |
| `{k:web-*}` | `k=~"web-.*"` | ✓ | `dd.filter.wildcard` | P0 | ☐ |
| `{k IN (a,b)}`, `k:a OR k:b` (same key) | `k=~"a\|b"` | ✓ | `dd.filter.in` | P1 | ☐ |
| `OR` across different keys | — | ✗ | `dd.filter.or-cross-key` | — | ☐ |
| `{$var}` with a tag prefix | `label=~"$var"` | ✓ | `dd.var.prefixed` | P0 | ☐ |
| `{$scope}` free-form variable | dropped (the default view is identical) | ~ | `dd.var.free-scope` | P0 | ☐ |
| `avg:` / `sum:` / `min:` / `max:` | `avg()` / `sum()` / `min()` / `max()` | ✓ | `dd.space-agg` | P0 | ☐ |
| no space aggregation | `avg()` (Datadog's default) | ✓ | `dd.space-agg.default` | P0 | ☐ |
| `p50:`…`p99:` (distributions) | — | ✗ | `dd.space-agg.percentile` | — | ☐ |
| `by {a,b}` | `by (a', b')` (keys mapped) | ✓ | `dd.group-by` | P0 | ☐ |
| `.rollup(avg\|max\|min\|sum\|count, N)` on a gauge | `*_over_time(x[Ns])` | ✓ | `dd.rollup` | P0 | ☐ |
| `.rollup(f)` with no interval | `*_over_time(x[$__interval])` | ≈ | `dd.rollup.auto` | P0 | ☐ |
| `.as_rate()` | `rate(...)` | ≈ | `dd.as-rate` | P0 | ☐ |
| `.as_count()` | `increase(x[$__interval])` (not integer, because of extrapolation) | ≈ | `dd.as-count` | P0 | ☐ |
| `.fill(null)` / default | default PromQL behaviour | ✓ | `dd.fill.null` | P0 | ☐ |
| `.fill(zero\|last\|linear)` | — | ~ | `dd.fill.other` | P1 | ☐ |
| arithmetic `+ - * /` with scalars | same operators | ✓ | `dd.arith.scalar` | P0 | ☐ |
| arithmetic between queries with identical grouping | binary operator (labels match one-to-one) | ✓ | `dd.arith.vector` | P0 | ☐ |
| arithmetic between queries with different grouping | binary operator with `on(...)` | ~ | `dd.arith.mismatched` | P0 | ☐ |
| formulas `query1 / query2 * 100` | references inlined | ✓ | `dd.formula` | P0 | ☐ |
| `abs`, `log2`, `log10` | same function | ✓ | `dd.fn.math` | P0 | ☐ |
| `clamp_min`, `clamp_max` | same function | ✓ | `dd.fn.clamp` | P1 | ☐ |
| `per_second(x)` | `rate(...)` | ≈ | `dd.fn.per-second` | P0 | ☐ |
| `per_minute(x)` / `per_hour(x)` | `60 *` / `3600 * rate(...)` | ≈ | `dd.fn.per-minute` | P0 | ☐ |
| `diff(x)` | `x - (x offset $__interval)` | ~ | `dd.fn.diff` | P0 | ☐ |
| `derivative(x)` | `deriv(x[$__interval])` | ~ | `dd.fn.derivative` | P1 | ☐ |
| `hour_before` / `day_before` / `week_before` / `timeshift` | `offset 1h` / `1d` / `1w` / `N` | ✓ | `dd.fn.timeshift` | P1 | ☐ |
| `top(x, N, 'mean'\|'max'\|…, 'desc'\|'asc')` | `topk`/`bottomk(N, avg_over_time((x)[$__range:]))` | ≈ | `dd.fn.top` | P0 | ☐ |
| `top5_mean`-style shorthands | as above | ≈ | `dd.fn.top-shorthand` | P1 | ☐ |
| `count_nonzero`, `count_not_null` | `count(x != 0)`, `count(x)` | ≈ | `dd.fn.count` | P1 | ☐ |
| `cumsum`, `integral`, `ewma_*`, `median_*`, `robust_trend`, `trend_line`, `outliers`, `anomalies`, `forecast` | — | ✗ | `dd.fn.unsupported` | — | ☐ |
| log / APM / RUM / event / process / SLO data sources | — | ✗ | `dd.datasource.unsupported` | — | ☐ |

## Widgets

| Datadog widget | Perses (Dash0) | Tier | rule_id | Prio | In Redis - Overview |
|---|---|---|---|---|---|
| `timeseries` (line / bars / area) | `TimeSeriesChart` (display mode, legend, y-axis min/max, unit) | ✓ | `dd.widget.timeseries` | P0 | 15 |
| `query_value` (aggregator avg/last/sum/min/max) | `StatChart` (calculation mean/last/sum/min/max) | ✓ | `dd.widget.query-value` | P0 | 10 |
| `note`, `free_text` | `Markdown` | ✓ | `dd.widget.note` | P0 | 7 |
| `group` | grid section with a title | ✓ | `dd.widget.group` | P0 | 7 |
| `toplist` | `BarChart` (horizontal, sorted) | ≈ | `dd.widget.toplist` | P0 | 1 |
| `query_table` | `Table` | ≈ | `dd.widget.query-table` | P1 | – |
| `change` | `Table` with an offset query | ~ | `dd.widget.change` | P1 | – |
| `alert_graph`, `alert_value` | `TimeSeriesChart` / `StatChart` of the monitor query | ≈ | `dd.widget.alert` | P1 | – |
| `image`, `iframe` | `Markdown` with a link | ≈ | `dd.widget.embed` | P1 | – |
| `list_stream`, `log_stream`, `event_stream` | placeholder `Markdown` | ✗ | `dd.widget.stream` | P0 (placeholder) | 2 |
| `distribution`, `heatmap` | placeholder `Markdown` | ✗ | `dd.widget.distribution` | P0 (placeholder) | 1 |
| `hostmap`, `check_status`, `manage_status`, `slo`, `servicemap`, `topology_map`, `geomap`, `funnel`, `sunburst`, `scatterplot`, `treemap` | placeholder `Markdown` | ✗ | `dd.widget.unsupported` | P0 (placeholder) | – |

**Layout:** `ordered` 12-column grid → Perses 24-column grid ✓ (`dd.layout.ordered`, P0). `free` layout → snapped to the grid ≈ (`dd.layout.free`, P1). Widgets without a layout → computed flow layout ≈ (`dd.layout.auto`, P0).

## Monitors

| Datadog | Dash0 check rule | Tier | rule_id | Prio |
|---|---|---|---|---|
| `type: metric alert` / `query alert` | rule with a PromQL `expression` | per query | `dd.monitor.metric` | P0 |
| `avg/max/min/sum(last_Nm)` with a pushdown-safe inner query | `*_over_time(x[Nm])` | ✓ | `dd.monitor.window.pushdown` | P0 |
| `avg/max/min/sum(last_Nm)` otherwise | `*_over_time((x)[Nm:1m])` subquery | ≈ | `dd.monitor.window.subquery` | P0 |
| comparator `>` `>=` | `… > $__threshold` | ✓ | `dd.monitor.comparator` | P0 |
| comparator `<` `<=` | `… < $__threshold` (⚠ verify Dash0 threshold ordering) | ~ until verified | `dd.monitor.comparator.below` | P0 |
| `thresholds.critical` / `.warning` | `thresholds.failed` / `.degraded` | ✓ | `dd.monitor.thresholds` | P0 |
| `critical_recovery` / `warning_recovery` | dropped; resolve timing may differ | ≈ | `dd.monitor.recovery` | P0 |
| `name` | `name` and `annotations.summary` | ✓ | `dd.monitor.name` | P0 |
| `message` (`{{value}}`, `{{tag.name}}`, `{{#is_alert}}`) | `annotations.description` (converted) | ≈ | `dd.monitor.message` | P0 |
| `@`-mentions, `escalation_message`, `renotify_interval` | removed; reported for manual notification setup | ≈ | `dd.monitor.notify` | P0 |
| `tags` `k:v` | `labels` `{k: v}` | ✓ | `dd.monitor.tags` | P0 |
| `priority` | `labels.priority` | ✓ | `dd.monitor.priority` | P0 |
| `require_full_window` | not representable; ignored | ≈ | `dd.monitor.full-window` | P0 |
| `evaluation_delay: N` | `offset Ns` inside the selectors | ≈ | `dd.monitor.eval-delay` | P1 |
| `new_host_delay`, `new_group_delay` | — | ≈ | `dd.monitor.group-delay` | P0 |
| `notify_no_data` | companion `absent_over_time` rule (P1); until then reported | ~ | `dd.monitor.no-data` | P1 |
| evaluation cadence (implicit, ~1m) | `interval: 1m`, `for: 0s` | ≈ | `dd.monitor.interval` | P0 |
| `service check`, `log alert`, `trace-analytics alert`, `composite`, `event alert`, `synthetics alert`, `slo alert`, `anomaly`/`forecast`/`outlier` queries | not imported | ✗ | `dd.monitor.unsupported` | — |

## Redis metric mapping (Datadog → OTel `redisreceiver`)

The source of truth is [`cli/openborder/mappings/datadog/redis.yaml`](../cli/openborder/mappings/datadog/redis.yaml). The table below covers the metrics used in Redis - Overview and the high-memory monitor. **OTel names must be checked against the collector version used in `demo/` and against live data in Dash0.**

| Datadog metric | OTel metric | Transform | Tier |
|---|---|---|---|
| `redis.mem.used` | `redis.memory.used` | none | ✓ |
| `redis.mem.maxmemory` | `redis.maxmemory` | none | ✓ |
| `redis.mem.rss` | `redis.memory.rss` | none | ✓ |
| `redis.mem.fragmentation_ratio` | `redis.memory.fragmentation_ratio` | none | ✓ |
| `redis.net.clients` | `redis.clients.connected` | none | ✓ |
| `redis.clients.blocked` | `redis.clients.blocked` | none | ✓ |
| `redis.net.slaves` | `redis.slaves.connected` | none | ✓ |
| `redis.stats.keyspace_hits` / `_misses` | `redis.keyspace.hits` / `.misses` | rate | ≈ |
| `redis.net.commands` | `redis.commands.processed` | rate | ≈ |
| `redis.net.rejected` | `redis.connections.rejected` | rate | ≈ |
| `redis.keys.evicted` / `.expired` | `redis.keys.evicted` / `.expired` | rate | ≈ |
| `redis.cpu.sys` / `.user` | `redis.cpu.time{state=sys\|user}` | rate | ≈ |
| `redis.rdb.changes_since_last` | `redis.rdb.changes_since_last_save` | none | ✓ |
| `redis.keys` | `redis.db.keys` | `sum` over `db` | ≈ |
| `redis.expires` | `redis.db.expires` | `sum` over `db` | ≈ |
| `redis.replication.delay`, `redis.replication.master_link_down_since_seconds` | `redis.replication.offset` family (to verify) | — | ~ |
| `redis.info.latency_ms`, `redis.slowlog.micros.*`, `redis.key.length` | none | — | ~ (unmapped) |

**Tags:** `host` → `host_name` (via the collector's `resourcedetection` processor). Other tags pass through with dots replaced by underscores and are flagged when they are unknown.

## Known semantic differences (detail)

- **Datadog's implicit rollup vs. PromQL's lookback:** Datadog averages raw points into display buckets, while PromQL takes the latest sample within a 5-minute lookback window at each step. For gauges this is treated as ✓; the convention is common and the visual difference is negligible.
- **Per-second gauges vs. counters:** the Datadog agent computes rates between check runs, while PromQL `rate()` averages over `$__rate_interval` and extrapolates. Spikes shorter than the window are smoothed (≈).
- **`top()` ranking:** Datadog ranks series once over the whole timeframe. `topk` over `avg_over_time(...[$__range:])` reproduces this for `mean`; a plain `topk` per step would not (≈ rather than ~).
- **Average of averages:** for monitor windows, `avg_over_time(avg(...))` and Datadog's bucketed evaluation agree when samples are evenly spaced (≈).
- **Tag value case:** Datadog lowercases tag values, but OTel attribute values keep their case. Exact-match filters on mixed-case values may stop matching. Reported per filter when a value contains uppercase characters (≈).
