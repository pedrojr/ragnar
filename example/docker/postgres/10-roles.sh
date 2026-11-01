#!/bin/bash
# Primário do Compose: papéis, banco e replicação. Roda uma vez, na criação do volume.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
  CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD}';
  CREATE ROLE ragnar_writer LOGIN PASSWORD '${RAGNAR_WRITER_PASSWORD}';
  CREATE ROLE ragnar_reader LOGIN PASSWORD '${RAGNAR_READER_PASSWORD}';
  ALTER ROLE ragnar_reader SET default_transaction_read_only = on;
  CREATE DATABASE ragnar OWNER ragnar_writer;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname ragnar -c "CREATE EXTENSION IF NOT EXISTS vector;"
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"
