-- ragnar_reader (MCP) só lê. O papel é criado pela infraestrutura (Compose ou CNPG),
-- já com default_transaction_read_only = on.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ragnar_reader') THEN
    GRANT USAGE ON SCHEMA public TO ragnar_reader;
    GRANT SELECT ON code_chunk, index_state, replication_probe TO ragnar_reader;
  ELSE
    RAISE NOTICE 'papel ragnar_reader não existe; GRANT ignorado';
  END IF;
END
$$;
