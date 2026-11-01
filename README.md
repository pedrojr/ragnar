# Exemplo: RAG de código com um banco de escrita e réplicas de leitura

Implementação simplificada e funcional do [plano](../plano-infra-rag.md), baseada no [guia](../rag-codigo-guia.md). Indexa repositórios git em Postgres + pgvector e expõe a busca a agentes por um servidor MCP somente leitura.

- **Um escritor só:** o indexador (`rag index`) grava no primário. O MCP lê só das réplicas, com um usuário que só tem `SELECT`.
- **Réplicas por volume de acesso:** no Kubernetes, o KEDA ajusta o número de réplicas pelas buscas por segundo.
- **Subida fácil:** `docker compose up` no dev; `./scripts/up.sh` no Kubernetes do Docker Desktop, sem Helm.

```text
agente ──HTTP+token──> rag-mcp ──SELECT──> réplicas (rag-db-ro) <──WAL── primário (rag-db-rw) <──COPY── rag index (host)
                          └──consulta──> rag-embedder <──chunks──────────────────────────────────────────┘
```

## Pré-requisitos

- Docker Desktop (Windows, macOS ou Linux). Para o Kubernetes: ligue em *Settings > Kubernetes*.
- `uv` (Python 3.12 é baixado sozinho) e `git`. No Windows, rode os scripts no Git Bash.
- Uns 6 GB de memória livres para o Docker. O embedder usa até 3 GB enquanto indexa.

## 1. Compose (desenvolvimento)

```bash
uv sync                                       # dependências locais (indexador e testes)
uv run rag init-env                           # cria .env (senhas, tokens) e acl.json; nunca commitar
docker compose up -d --build --scale db-replica=2
./scripts/index.sh /path/to/repo myapp        # base inicial; rode de novo para o incremento
./scripts/smoke.sh                            # 9 verificações ponta a ponta
```

Portas no host, todas em `127.0.0.1`:

| Porta | Serviço | Quem usa |
| --- | --- | --- |
| 8080 | MCP (`/mcp`, `/metrics`, `/readyz`) | agentes |
| 15432 | primário do Postgres | só o indexador no host |
| 8001 | embedder (`/v1/embeddings`) | só o indexador no host |

As réplicas não são publicadas. Escala manual: `docker compose up -d --scale db-replica=3`.

## 2. Kubernetes do Docker Desktop (escala automática)

```bash
docker compose down                           # libera a porta 8080
./scripts/up.sh                               # operadores, imagens, segredos, banco, MCP
./scripts/index.sh /path/to/repo myapp --target k8s
./scripts/smoke.sh --target k8s
```

O `up.sh` instala, com versão fixa: metrics-server 0.9.0, CloudNativePG 1.30.1 e KEDA 2.21.0. Depois cria:

| Recurso | O que é | Escala |
| --- | --- | --- |
| `Cluster/rag-db` (CNPG) | 1 primário + réplicas; serviços `rag-db-rw` e `rag-db-ro` | KEDA, de 2 a 4 instâncias, por buscas/s |
| `Deployment/rag-mcp` | servidor MCP, `LoadBalancer` em `localhost:8080` | HPA por CPU, de 1 a 5 pods |
| `Deployment/rag-embedder` | embedder Python (fastembed/ONNX) | HPA por CPU, de 1 a 3 pods |
| `Deployment/prometheus` | coleta `rag_search_total` dos pods do MCP | fixo |
| `CronJob/rag-indexer-exemplo` | modelo de indexação com URL git, suspenso | um Job por vez |

Teste de escala:

```bash
kubectl apply -f k8s/jobs/load.yaml           # 16 clientes MCP, 40 buscas/s, 7 min
kubectl -n rag get hpa,scaledobject,cluster -w
kubectl -n rag logs -f job/rag-load           # buscas/s, p50, p95, erros a cada 10 s
```

Regra do banco: 1 réplica a cada 10 buscas/s (`k8s/base/db-scaledobject.yaml`). Sobe uma réplica por minuto e desce uma a cada 2 minutos, depois de 5 minutos estável. Remover tudo: `./scripts/down.sh` (com `--all`, também os operadores).

Resultado medido (Docker Desktop, 20 CPUs, 2026-10-29), 40 buscas/s por 7 minutos:

| Tempo | Banco (instâncias) | MCP (pods) | Observação |
| --- | --- | --- | --- |
| 0 s | 2 (1 primário + 1 réplica) | 1 | início da carga |
| ~90 s | 3 | 1 → 4 | MCP a 243% da CPU pedida |
| ~150 s | 4 (3 réplicas) | 4 → 5 | réplicas novas clonando o primário |
| ~210 s | 4 prontas | 5 | 40 buscas/s, p50 23 ms, p95 ~45 ms |
| fim + ~7 min | 4 → 3 → 2 | 1 | descida gradual |

16.343 buscas, 0 erros. O p95 total (407 ms) vem do primeiro minuto, com 1 pod do MCP saturado.

As consultas da carga se repetem, então o cache de embedding do MCP absorve o embedder. Carga com consultas variadas desloca o gargalo para o embedder, que tem HPA próprio.

**Indexação no cluster:** `index.sh --target k8s` usa `port-forward`, que prende o tráfego num pod. O HPA do embedder sobe para 3 pods, mas só 1 trabalha. Para repositórios grandes, indexe com o CronJob dentro do cluster (`k8s/base/indexer-cronjob.yaml`): ele passa pelo Service e usa todos os pods (`EMBED_CONCURRENCY`).

## 3. Plugin para o Claude Code

```text
/plugin marketplace add /path/to/repo
/plugin install code-rag@rag-local
```

Defina antes de abrir o Claude Code:

```bash
export RAG_MCP_TOKEN=<valor de RAG_MCP_TOKEN no .env>
export RAG_HOME=/path/to/repo       # usado pelas skills
# RAG_MCP_URL é opcional; padrão http://127.0.0.1:8080/mcp
```

O plugin traz:

- o servidor MCP `code-rag`, com as ferramentas `search_code`, `find_symbol` e `list_repos`;
- a skill `rag-indexar`: indexa um repositório e libera acesso por token;
- a skill `rag-avaliar`: compara busca com e sem RAG (Recall@10, MRR, IC95%).

Sugestão para o `AGENTS.md`/`CLAUDE.md` do projeto: "Para localizar código por conceito de negócio, use `search_code` antes de `grep`."

## Comandos

| Comando | Faz | Credencial |
| --- | --- | --- |
| `rag init-env` | cria `.env` e `acl.json` | — |
| `rag migrate` | aplica `src/raglib/migrations` no primário | `RAG_DSN_WRITE` |
| `rag index <origem> <nome> [--full] [--ref]` | base inicial ou incremento, com gitleaks | `RAG_DSN_WRITE` |
| `rag token --name <time> --repos a,b` | token novo e entrada para `acl.json` | — |
| `rag search "<consulta>" --repo <nome>` | busca direta, para depuração | `RAG_DSN_READ` |
| `rag mcp` / `rag embedder` | servidores HTTP | `RAG_DSN_READ` / — |
| `rag smoke` / `rag load` | verificação e carga via MCP | `RAG_MCP_TOKEN` |

## Como o indexador funciona

1. Lê os arquivos do commit da `main` com `git ls-tree` e `git cat-file`. Nada não commitado entra.
2. Primeira vez, `--full` ou modelo trocado: todos os arquivos. Depois: só `git diff` desde o commit indexado.
3. Roda gitleaks nos arquivos que vai indexar. Achou segredo: para (binário local ou imagem `ghcr.io/gitleaks/gitleaks`).
4. Corta código por AST (tree-sitter: Go, Java, C#, Python) e Markdown por seção. Extrai as definições para `code_symbol`.
5. Gera os embeddings fora do banco. Depois, numa transação curta: trava o repositório, troca os chunks, grava o commit.

## Testes

```bash
uv run pytest                                  # unitários (sem banco)
set -a; . ./.env; set +a
RAG_TEST_ADMIN_DSN="postgresql://postgres:$POSTGRES_PASSWORD@127.0.0.1:15432/postgres" uv run pytest   # + integração
```

A integração cria e apaga o banco `rag_test` no primário do Compose.

## Modelo de embedding

Padrão: `jinaai/jina-embeddings-v2-base-code` (768 dimensões, treinado em código), embutido na imagem `rag-embedder`.

- Em CPU, cada chunk custa cerca de 1 s com 8 threads. A base inicial do yak (728 chunks) leva minutos; o incremento, segundos.
- A entrada é cortada em 512 tokens (`EMBED_MAX_TOKENS`). O BM25 continua vendo o chunk inteiro.
- Trocar de modelo: `EMBED_MODEL` e `EMBED_DIM` no `.env` e em `k8s/base/embed.env`, nova imagem, banco novo se a dimensão mudar, e `--full`.
- Provedor externo ou Ollama: `EMBED_URL` para qualquer endpoint `/v1/embeddings`. Isso envia o código ao provedor.

## Limites do exemplo

- Token estático por cliente. Produção: OAuth do MCP e grupos do IdP.
- Sem TLS, NetworkPolicy, backup e PodDisruptionBudget. O índice é derivado do git: recuperar = reindexar.
- O Compose distribui leitura entre réplicas pelo DNS do Docker, de forma aproximada.
- Compose e Kubernetes usam a porta 8080: um de cada vez.
