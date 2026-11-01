-- Definitions extracted from the AST: find_symbol finds functions even when they share a chunk.
CREATE TABLE code_symbol (
  id          bigserial PRIMARY KEY,
  repo        text NOT NULL,
  path        text NOT NULL,
  symbol      text NOT NULL,
  name        text NOT NULL,
  node_type   text NOT NULL,
  kind        text NOT NULL,
  start_line  int  NOT NULL,
  end_line    int  NOT NULL,
  signature   text NOT NULL,
  commit_sha  text NOT NULL
);
CREATE INDEX code_symbol_name_idx ON code_symbol (repo, lower(name) text_pattern_ops);
CREATE INDEX code_symbol_repo_path_idx ON code_symbol (repo, path);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ragnar_reader') THEN
    GRANT SELECT ON code_symbol TO ragnar_reader;
  END IF;
END
$$;
