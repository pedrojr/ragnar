"""`ragnar gc`: removes cached rows that no index uses any more.

Both caches only grow. summary_cache keeps every summary a model ever wrote, including those of files
that changed, of another model or of another summary language. embedding_checkpoint keeps the vectors
of runs that stopped and never ran again. Rows younger than the age limit stay: an indexing run that is
still going writes its summaries and vectors before its chunks commit.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

# a summary chunk is two header lines (file, symbol) and the cached summary text
USED_SUMMARIES = r"""SELECT DISTINCT regexp_replace(content, '^[^\n]*\n[^\n]*\n', '') AS summary
                     FROM code_chunk WHERE kind = 'summary'"""


@dataclass
class Cleanup:
    summaries: int
    checkpoints: int
    xref_names: int


def collect(dsn: str, older_than_days: float = 7, dry_run: bool = False) -> Cleanup:
    age = {"days": older_than_days}
    with psycopg.connect(dsn) as conn:
        summaries = conn.execute(
            f"""WITH used AS ({USED_SUMMARIES})
                SELECT count(*) FROM summary_cache s
                WHERE s.created_at < now() - make_interval(days => %(days)s)
                  AND NOT EXISTS (SELECT 1 FROM used u WHERE u.summary = s.summary)""", age).fetchone()[0]
        checkpoints = conn.execute("SELECT count(*) FROM embedding_checkpoint "
                                   "WHERE created_at < now() - make_interval(days => %(days)s)", age).fetchone()[0]
        xref_names = conn.execute("SELECT count(*) FROM code_xref_name x "
                                  "WHERE NOT EXISTS (SELECT 1 FROM index_state s WHERE s.repo = x.repo)").fetchone()[0]
        if not dry_run:
            conn.execute(
                f"""WITH used AS ({USED_SUMMARIES})
                    DELETE FROM summary_cache s
                    WHERE s.created_at < now() - make_interval(days => %(days)s)
                      AND NOT EXISTS (SELECT 1 FROM used u WHERE u.summary = s.summary)""", age)
            conn.execute("DELETE FROM embedding_checkpoint WHERE created_at < now() - make_interval(days => %(days)s)", age)
            conn.execute("DELETE FROM code_xref_name x WHERE NOT EXISTS (SELECT 1 FROM index_state s WHERE s.repo = x.repo)")
    return Cleanup(summaries, checkpoints, xref_names)


REPO_TABLES = ("code_chunk", "code_symbol", "code_edge", "code_xref_name")


def remove_repo(dsn: str, repo: str, dry_run: bool = False) -> dict[str, int] | None:
    """Takes a repository out of the index: its chunks, symbols, edges and state. None when it is not indexed.

    Runs under the repository's indexing lock, so it waits for a run in progress. The cached summaries and
    vectors stay for `ragnar gc`, and the other repositories' edges to its names go with the next link."""
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (repo,))
        if conn.execute("SELECT 1 FROM index_state WHERE repo = %s", (repo,)).fetchone() is None:
            return None
        verb = "SELECT count(*) FROM" if dry_run else "DELETE FROM"
        out = {}
        for table in REPO_TABLES:
            cur = conn.execute(f"{verb} {table} WHERE repo = %s", (repo,))
            out[table] = cur.fetchone()[0] if dry_run else cur.rowcount
        if not dry_run:
            conn.execute("DELETE FROM index_state WHERE repo = %s", (repo,))
    return out
