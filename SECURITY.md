# Security

[Português (Brasil)](SECURITY.pt-BR.md)

## Reporting a vulnerability

Do not open a public issue. Use [GitHub private vulnerability reporting](https://github.com/pedrojr/ragnar/security/advisories/new) with the steps to reproduce the problem.

Expect a first reply within 7 days. Ragnar is maintained on a best-effort basis, and only the latest release gets fixes.

## What Ragnar protects

- **Secrets in code.** The indexer runs gitleaks on normalized text before indexing, and a finding stops the run. The index stays on the last good commit. A server can choose `index.secret_action = "skip"` instead, which leaves the files with findings out.
- **Write access.** The MCP server connects with a role that can only read, on replicas, and it never receives the write password.
- **Repository access.** Tokens are stored as hashes. Each one lists the repositories it may read.
- **Repository-driven configuration.** A repository can change only how it is chunked and which languages are indexed, so it cannot redirect embeddings or turn off the secret scan.
- **Prompt injection through code.** Retrieved content goes inside delimiters, closing tags in it are escaped, and it is marked as data. This reduces the risk, but it cannot remove it. An agent may still follow instructions planted in a repository, so review what your agents do.

## Before you expose a server

The [example](example/README.md) is not hardened. Under Compose it binds to `127.0.0.1`. Under Kubernetes, Docker Desktop publishes the MCP `LoadBalancer` on every network interface. On a shared network:

1. Put a TLS proxy in front of the MCP server. Tokens travel in a header.
2. Keep Postgres, the embedder and `/metrics` off the network.
3. Issue one token per team, with the fewest repositories possible. Grant a repository only to people who can already read it.
4. Treat the database and the audit logs like source code. The index holds code, and the logs hold search text.
5. Rotate tokens when people leave. Change `acl.json`, then restart the server.

## Hosted model providers

Embeddings, file summaries, reranking, contextual retrieval and query expansion send code or questions to the configured endpoint. The default setup keeps everything on your machines. Before you point any of these at a hosted provider, confirm it meets your policy.
