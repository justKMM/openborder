#!/bin/sh
# Steady, deterministic Redis traffic so every migrated panel has something to show:
#   - writes keys 0..4999 with ~15 KB values and a TTL   -> keys, expirations, memory growth,
#     and (once the 64 MB limit is hit) evictions
#   - reads keys 0..7999 in a scrambled order             -> hits for 0..4999, misses for 5000..7999
#   - a blocking pop with a short timeout                  -> briefly blocked clients
set -eu
HOST="${REDIS_HOST:-redis}"
VALUE="$(head -c 15000 /dev/zero | tr '\0' x)"
i=0
echo "loadgen: sending traffic to $HOST"
while true; do
  i=$((i + 1))
  redis-cli -h "$HOST" SET "key:$((i % 5000))" "$VALUE" EX 300 > /dev/null
  redis-cli -h "$HOST" GET "key:$(((i * 7919) % 8000))" > /dev/null
  redis-cli -h "$HOST" GET "key:$(((i * 104729) % 8000))" > /dev/null
  if [ $((i % 50)) -eq 0 ]; then
    redis-cli -h "$HOST" BLPOP never-exists 1 > /dev/null &
  fi
  sleep 0.05
done
