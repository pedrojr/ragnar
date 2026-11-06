-- literal_text: string literals of a chunk (messages, captions, SQL), weighted B by the lexical ranking.
-- A symptom ("Cliente sem limite de crédito") names a string, and in the body it weighs as little as any word.
-- Rewrites code_chunk, like migration 005. The chunker version changes too, so the next run fills the column.
ALTER TABLE code_chunk ADD COLUMN literal_text text NOT NULL DEFAULT '';

DROP INDEX code_chunk_tsv_idx;
ALTER TABLE code_chunk DROP COLUMN tsv;
ALTER TABLE code_chunk ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
  setweight(to_tsvector('simple', head_text), 'A')
  || setweight(to_tsvector('simple', literal_text), 'B')
  || setweight(to_tsvector('simple', search_text), 'D')
) STORED;
CREATE INDEX code_chunk_tsv_idx ON code_chunk USING gin (tsv);
