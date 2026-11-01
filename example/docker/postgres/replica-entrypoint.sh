#!/bin/bash
# Disposable Compose replica: clones the primary on every start and runs as a hot standby.
set -euo pipefail
export PGPASSWORD="$REPLICATION_PASSWORD"
until pg_isready -q -h db-primary -U replicator -d postgres; do sleep 1; done
rm -rf "${PGDATA:?}"/*
pg_basebackup -h db-primary -U replicator -D "$PGDATA" -R -X stream
chmod 700 "$PGDATA"
exec postgres -c hot_standby_feedback=on -c max_standby_streaming_delay=30s
