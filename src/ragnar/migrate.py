"""Applies the SQL migrations in order, once each, on the primary."""

from __future__ import annotations

from importlib import resources

import psycopg

from .config import load_settings

MIGRATION_LOCK = "ragnar.migrate"


def pending(conn: psycopg.Connection, dim: int) -> list[tuple[str, str]]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    files = sorted(
        (f for f in resources.files("ragnar.migrations").iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    return [(f.name, f.read_text(encoding="utf-8").replace("{{EMBED_DIM}}", str(dim))) for f in files if f.name not in done]


def migrate(dsn: str) -> list[str]:
    applied = []
    with psycopg.connect(dsn) as conn:
        # one migrator at a time, even with two Jobs running
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (MIGRATION_LOCK,))
        for name, sql in pending(conn, load_settings().embedding.dim):
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
            applied.append(name)
    return applied


def resize_embeddings(dsn: str) -> int:
    """Clears the index and changes the vector dimension. Every repository must be indexed again."""
    dim = load_settings().embedding.dim
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (MIGRATION_LOCK,))
        conn.execute("TRUNCATE code_chunk, code_symbol, code_edge, index_state, embedding_checkpoint")
        conn.execute("DROP INDEX IF EXISTS code_chunk_embedding_idx")
        conn.execute(f"ALTER TABLE code_chunk ALTER COLUMN embedding TYPE vector({int(dim)})")
        conn.execute("CREATE INDEX code_chunk_embedding_idx ON code_chunk USING hnsw (embedding vector_cosine_ops)")
    return dim
