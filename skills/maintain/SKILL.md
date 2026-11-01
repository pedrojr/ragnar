---
name: maintain
description: Checks the health of a ragnarized project and its index, and fixes what it finds. Covers settings, .gitignore, index freshness, worker failures and access. Use for "check Ragnar", "the index is stale", "search is failing", "manutenção da base", "ragnar doctor", or after upgrading the plugin.
---

# Maintain the index

## Steps

1. Call `list_repos`. Find this project and note its indexed commit. A failure here means the MCP server is unreachable or the token is wrong: go to "Common problems".
2. Fetch, then run the local checks:
   ```bash
   git fetch --quiet
   ragnar doctor --indexed-commit <sha>
   ```
3. Act on each line:

   | Result | Action |
   | --- | --- |
   | `[fail] ragnar.toml` | Run the `ragnarize` skill, or fix the setting the message names |
   | `[warn] .gitignore` | Run `ragnar init`: it keeps the settings and restores the block |
   | `[fail] branch` | `project.branch` does not exist here; fix it or fetch |
   | `[warn] index ... behind` | Check the worker (below) |
   | `[warn] index ... not in this clone` | Fetch. If it persists, the branch was rewritten, and the next pass does a full reindex by itself |

4. Check the worker when the index falls behind:
   ```bash
   docker compose -f "$RAGNAR_HOME/example/compose.yaml" logs --tail 50 indexer
   kubectl -n ragnar get cronjob,jobs                 # Kubernetes
   ```
5. Report what you found, what you fixed, and what needs the user.

## Common problems

| Symptom | Cause | Action |
| --- | --- | --- |
| `401` or `missing or invalid token` | Wrong token, or the ACL was not reloaded | Set `mcp_token` in `/plugin` config; restart `ragnar-mcp` after changing `acl.json` |
| `no permission for repository` | The token does not list this repository | Add it to `repos` in `acl.json` and restart `ragnar-mcp` |
| `read database unavailable` | Replicas are down | `docker compose ps`; `kubectl -n ragnar get cluster` |
| `secret found` in the worker logs | A credential reached the branch. The index stays on the last good commit | Rotate the credential and remove it; the next pass resumes |
| `embedder unavailable` | Embedder down or out of memory | Read its logs; lower `RAGNAR_EMBED_BATCH` |
| Worker logs `... is empty` | `token_env` names a variable that is not set | Set `RAGNAR_GIT_TOKEN` in `.env` and restart `indexer` |

## Periodic care

- After the first month, run the `evaluate` skill on recent commits. Codebases drift, and so does retrieval quality.
- Run the `tune` skill after a large refactor or a model change.
- Review `index.exclude` when the language mix changes: `ragnar init --force` shows the new detection, but it rewrites `ragnar.toml`, so confirm with the user first.
