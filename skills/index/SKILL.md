---
name: index
description: Builds the first index of a repository or refreshes it on demand, and gives a team access through a token. Runs the secret scan, the initial build or the increment, and confirms the result with list_repos. Use for "index this repository", "train the RAG", "treinar a base", "update the index", "reindex", "give team X access".
---

# Index a repository

The index worker keeps a repository current once it is listed in `repos.toml`. Use this skill for the first build, or to refresh without waiting for the worker.

## Inputs

- Local path or git URL of the repository, and its name in the index. The name defaults to `project.name` in `.ragnar/config.toml`.
- Target: `compose` or `k8s`. Find it with `docker compose -f "$RAGNAR_HOME/example/compose.yaml" ps -q ragnar-mcp` (output means Compose) or `kubectl -n ragnar get deploy ragnar-mcp`.
- `RAGNAR_HOME`: the folder of a Ragnar clone. If it is not set, ask the user where the infrastructure lives.

If the user has no infrastructure yet, use the `infra` skill first.

## Rules

1. **Only committed code.** The indexer reads the branch commit, never the working tree.
2. **A secret stops the run.** Show the files and lines gitleaks reported. Ask the user to rotate the credential and remove it from the repository. Never use `--no-secret-scan` to get past a finding.
3. **The index is source code.** Grant a token access to a repository only if its owner can already read that repository.
4. **Confirm before `--full` on a large repository.** On CPU, the bundled embedder takes about one second per chunk.

## Steps

1. Check the repository:
   ```bash
   git -C <path> rev-parse --verify <branch>
   git -C <path> ls-files | wc -l
   ```
2. Index. The first run builds everything; later runs handle only what changed:
   ```bash
   cd "$RAGNAR_HOME/example"
   ./scripts/index.sh <path-or-url> <name> --ref <branch>                 # Compose
   ./scripts/index.sh <path-or-url> <name> --ref <branch> --target k8s    # Kubernetes
   ```
   The last line reads `<name>: full|incremental|unchanged at <sha>: N files, M chunks written, ...`.
3. Keep it current. Add the repository to `$RAGNAR_HOME/example/repos.toml`, then run `docker compose restart indexer`. On Kubernetes, run `./scripts/up.sh`, which reloads the file.
4. Grant access for a new team:
   ```bash
   ragnar token --name <team> --repos <name>[,<other>]
   ```
   Tell the user to pass the token over a secure channel. Add the printed JSON line to `clients` in `$RAGNAR_HOME/example/acl.json`. Reload with `docker compose restart ragnar-mcp`, or `./scripts/up.sh` on Kubernetes.
5. Confirm with `list_repos`: the repository appears with its commit and chunk count.
6. Try `search_code` with a business term from the repository.

## When a full reindex happens

Ragnar reindexes everything by itself when the embedding model or a chunking setting changes. A model with another dimension also needs:

```bash
cd "$RAGNAR_HOME/example" && docker compose run --rm tools migrate --resize-embeddings   # CLEARS the whole index
```

Confirm with the user first: every repository must be indexed again.

## Common problems

| Symptom | Cause | Action |
| --- | --- | --- |
| `embedder unavailable` | Embedder down or out of memory | `docker compose ps embedder`, or `kubectl -n ragnar get pods`; read the logs |
| `another indexer wrote ...` | Two runs on the same repository | Run again; the second run sees the new state |
| `secret found` | gitleaks found a credential | Rule 2 |
| `timed out waiting for the condition on pods/ragnar-index-...` | The indexer pod did not start (k8s) | `kubectl -n ragnar describe pod -l app=ragnar-index`; run `./scripts/up.sh` if the image or a Secret is missing |
| `returned dimension N, expected M` | Model and `embedding.dim` disagree | Fix `embedding.dim`, then see the section above |
