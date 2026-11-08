# Test fixtures

`datadog/redisdb/` holds two unmodified assets from
[DataDog/integrations-core](https://github.com/DataDog/integrations-core) (`redisdb/assets/`),
which is published under the BSD 3-Clause license:

- `dashboards/overview.json`: "Redis - Overview"
- `monitors/high_mem.json`: "[Redis] High memory consumption"

They are used for offline tests and for `openborder plan --from-dir`.
