-- content_hash: an unchanged chunk keeps its vector through a reindex, so settings changes stop paying for embeddings.
-- head_text: split path and symbol words, weighted above the body by the lexical ranking.
ALTER TABLE code_chunk ADD COLUMN content_hash text NOT NULL DEFAULT '';
ALTER TABLE code_chunk ADD COLUMN head_text text NOT NULL DEFAULT '';

DROP INDEX code_chunk_tsv_idx;
ALTER TABLE code_chunk DROP COLUMN tsv;
ALTER TABLE code_chunk ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
  setweight(to_tsvector('simple', head_text), 'A') || setweight(to_tsvector('simple', search_text), 'D')
) STORED;
CREATE INDEX code_chunk_tsv_idx ON code_chunk USING gin (tsv);
CREATE INDEX code_chunk_hash_idx ON code_chunk (repo, content_hash);

-- Run statistics, for the reports: what a run left out and why.
ALTER TABLE index_state ADD COLUMN skipped jsonb NOT NULL DEFAULT '{}';
