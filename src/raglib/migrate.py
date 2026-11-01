"""Aplica as migrações SQL em ordem, uma vez cada, no primário."""

from __future__ import annotations

from importlib import resources

import psycopg

from .config import embed_config


def pending(conn: psycopg.Connection) -> list[tuple[str, str]]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    files = sorted(
        (f for f in resources.files("raglib.migrations").iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    dim = str(embed_config().dim)
    return [(f.name, f.read_text(encoding="utf-8").replace("{{EMBED_DIM}}", dim)) for f in files if f.name not in done]


def migrate(dsn: str) -> list[str]:
    applied = []
    with psycopg.connect(dsn) as conn:
        # um migrador por vez, mesmo com dois Jobs rodando
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('raglib.migrate'))")
        for name, sql in pending(conn):
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
            applied.append(name)
    return applied
