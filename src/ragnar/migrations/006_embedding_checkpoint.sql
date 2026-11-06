-- Vectors of a run that has not finished yet. A first build of a large repository takes hours on a CPU:
-- when it stops, the next run starts from here instead of from zero. Rows leave when their run commits.
CREATE TABLE embedding_checkpoint (
  model        text NOT NULL,
  content_hash text NOT NULL,
  embedding    text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (model, content_hash)
);
