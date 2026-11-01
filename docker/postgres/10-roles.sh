#!/bin/bash
# Primário do Compose: papéis, banco e replicação. Roda uma vez, na criação do volume.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
  CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD}';
  CREATE ROLE rag_writer LOGIN PASSWORD '${RAG_WRITER_PASSWORD}';
  CREATE ROLE rag_reader LOGIN PASSWORD '${RAG_READER_PASSWORD}';
  ALTER ROLE rag_reader SET default_transaction_read_only = on;
  CREATE DATABASE rag OWNER rag_writer;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname rag -c "CREATE EXTENSION IF NOT EXISTS vector;"
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"
