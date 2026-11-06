-- Model-written file summaries, by the hash of the file text: a full reindex reuses them, so a settings
-- change does not pay for every summary again.
CREATE TABLE summary_cache (
  model       text NOT NULL,
  source_hash text NOT NULL,
  summary     text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (model, source_hash)
);
