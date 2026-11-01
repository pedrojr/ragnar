-- ragnar_reader (MCP) only reads. The infrastructure creates the role (Compose or CNPG)
-- with default_transaction_read_only = on.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ragnar_reader') THEN
    GRANT USAGE ON SCHEMA public TO ragnar_reader;
    GRANT SELECT ON code_chunk, index_state, replication_probe TO ragnar_reader;
  ELSE
    RAISE NOTICE 'role ragnar_reader does not exist; GRANT skipped';
  END IF;
END
$$;
