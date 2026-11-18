-- Cross-repository references: a routine of one repository names a declaration of another (edge kind xref).
-- code_xref_name holds, per repository, the names of other repositories its xref edges were matched against.
-- When another repository declares a new name, only the files that hold it are scanned again.
CREATE TABLE code_xref_name (
  repo text NOT NULL,
  name text NOT NULL,
  PRIMARY KEY (repo, name)
);

-- callers across repositories look an xref edge up by name alone
CREATE INDEX code_edge_xref_idx ON code_edge (dst_name) WHERE kind = 'xref';
