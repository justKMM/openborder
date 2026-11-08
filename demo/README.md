# Live Redis data for the migrated dashboard

A migrated dashboard that shows nothing proves only that the import worked. This stack feeds it real data:

- **redis**: Redis 7 with `maxmemory 64mb`, so evictions really happen.
- **loadgen**: steady traffic with hits, misses, expiring keys and a few blocked clients (`loadgen.sh`).
- **otel-collector**: OpenTelemetry Collector 0.165.0 with the `redis` receiver, sending to Dash0 over OTLP.

## Run it

```bash
cd demo
cp .env.example .env     # then fill in DASH0_OTLP_ENDPOINT and DASH0_AUTH_TOKEN (the file is gitignored)
docker compose up -d
docker compose logs -f otel-collector      # look for export errors
docker compose down                        # stop
```

`DASH0_OTLP_ENDPOINT` is the OTLP/gRPC endpoint from **Dash0 → Settings → Organization → Endpoints** (host and port, usually `:4317`). It is a different host from `DASH0_API_URL`, which `openborder apply` uses. After a minute or two the migrated "Redis - Overview" dashboard should fill in; pick `redis-demo` in its `host` variable.

## Look at the metrics without sending anything

```bash
OTEL_CONFIG=otel-collector.local.yaml docker compose up -d
docker compose logs otel-collector        # the debug exporter prints every metric, type and attribute
docker compose down -v
```

`otel-collector.local.yaml` has no exporter that leaves your machine.

## What is verified, and what is not

| | State |
|---|---|
| The stack starts; Redis, the load generator and the collector run without errors | verified (Docker 27, Windows) |
| `redisreceiver` emits every OTel metric our Redis mapping uses, with the attributes and types it assumes (`redis.maxmemory` once enabled; `state=sys\|user` on `redis.cpu.time`; monotonic cumulative sums for the counters) | verified against the real output; saved in `cli/tests/fixtures/otel/redisreceiver.json` and checked by `cli/tests/test_mapping_vs_collector.py` |
| `otel-collector.yaml` (the Dash0 exporter) | **not run**: it needs your endpoint and token. The `Authorization: Bearer` header follows Dash0's guidance; the `Dash0-Dataset` header is an assumption (only third-party sources mention it for the collector) |
| The migrated dashboard renders this data in Dash0 | **not verified yet** |

## Notes

- `host.name` is set to `redis-demo` (`DEMO_HOST_NAME`) so the dashboard's `host` variable has a stable value. In a real deployment use the collector's `resourcedetection` processor.
- `redis.maxmemory` is an optional metric in `redisreceiver`; it is enabled in both configs because the "High memory consumption" monitor needs it.
- Datadog's `redis.info.latency_ms`, `redis.slowlog.*` and `redis.replication.delay` have no counterpart in `redisreceiver`, so those panels stay empty by design (they are flagged ~ in the plan).
- `.gitattributes` keeps these files on LF line endings; with git's `autocrlf` on Windows a CRLF `loadgen.sh` would not run in the container.
