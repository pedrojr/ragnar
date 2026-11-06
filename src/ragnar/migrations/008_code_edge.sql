-- Code graph, found by name: routine -> referenced declaration (ref), form control -> handler (event),
-- routine -> table (sql_read, sql_write). dst_name is lowercase; it resolves against code_symbol by name.
CREATE TABLE code_edge (
  id          bigserial PRIMARY KEY,
  repo        text NOT NULL,
  path        text NOT NULL,
  line        int  NOT NULL,
  src_symbol  text NOT NULL,   -- qualified symbol around the reference; empty at file level
  dst_name    text NOT NULL,
  kind        text NOT NULL,   -- ref | event | sql_read | sql_write
  text        text NOT NULL,   -- the source line, trimmed
  commit_sha  text NOT NULL
);
CREATE INDEX code_edge_dst_idx ON code_edge (repo, dst_name);
CREATE INDEX code_edge_src_idx ON code_edge (repo, path, line);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ragnar_reader') THEN
    GRANT SELECT ON code_edge TO ragnar_reader;
  END IF;
END
$$;
