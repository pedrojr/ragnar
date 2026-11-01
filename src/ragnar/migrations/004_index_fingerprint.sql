-- Hash of the settings that shape the chunks: when it changes, the next run reindexes everything.
ALTER TABLE index_state ADD COLUMN fingerprint text NOT NULL DEFAULT '';
