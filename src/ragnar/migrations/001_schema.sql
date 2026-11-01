-- Schema da seção 5.2 do guia. A dimensão do vetor acompanha o modelo (EMBED_DIM).
-- Trocar de modelo com outra dimensão = nova migração + reindexação completa.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE code_chunk (
  id           bigserial PRIMARY KEY,
  repo         text NOT NULL,
  path         text NOT NULL,
  symbol       text,
  language     text NOT NULL,
  kind         text NOT NULL DEFAULT 'code',   -- code | test | doc
  start_line   int  NOT NULL,
  end_line     int  NOT NULL,
  commit_sha   text NOT NULL,
  content      text NOT NULL,                  -- cabeçalho + trecho
  search_text  text NOT NULL,                  -- content + identificadores separados
  tsv          tsvector GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
  embedding    vector({{EMBED_DIM}}) NOT NULL   -- trocado por migrate.py (EMBED_DIM)
);

CREATE INDEX code_chunk_embedding_idx ON code_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX code_chunk_tsv_idx ON code_chunk USING gin (tsv);
CREATE INDEX code_chunk_repo_path_idx ON code_chunk (repo, path);
CREATE INDEX code_chunk_symbol_idx ON code_chunk (repo, lower(symbol) text_pattern_ops);

CREATE TABLE index_state (
  repo        text PRIMARY KEY,
  commit_sha  text NOT NULL,
  embed_model text NOT NULL,
  indexed_at  timestamptz NOT NULL DEFAULT now()
);

-- Uma linha gravada no primário e lida nas réplicas: mede o atraso da replicação.
CREATE TABLE replication_probe (
  id         int PRIMARY KEY,
  written_at timestamptz NOT NULL
);
