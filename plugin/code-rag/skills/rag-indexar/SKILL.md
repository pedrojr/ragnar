---
name: rag-indexar
description: Coloca um repositório no índice RAG de código ou atualiza o índice existente. Roda a varredura de segredos, a indexação (base inicial ou incremento desde o último commit indexado), libera o acesso por token e confere o resultado com list_repos. Use para "indexar o repositório X", "atualizar o índice do RAG", "dar acesso ao RAG para o time Y", "/rag-indexar".
compatibility: Requer o exemplo do RAG (variável RAG_HOME apontando para a pasta exemplo), uv, git e Docker. Para o alvo k8s, kubectl com o contexto docker-desktop.
metadata:
  kit: code-rag
  version: "0.1.0"
---

# Indexar repositório no RAG

## Entradas

- Caminho local ou URL git do repositório. Se faltar, pergunte.
- Nome no índice (padrão: nome da pasta, em minúsculas).
- Alvo: `compose` ou `k8s`. Descubra com `docker compose -f "$RAG_HOME/compose.yaml" ps -q rag-mcp` (saída não vazia = compose) ou `kubectl -n rag get deploy rag-mcp`. Os dois no ar ao mesmo tempo não funcionam: os dois usam a porta 8080.
- `RAG_HOME` definido. Se faltar, pergunte onde está a pasta `exemplo`.

## Regras

1. **Só a branch `main`.** O indexador lê o commit da branch, nunca o diretório de trabalho. Mudança não commitada não entra.
2. **Segredo encontrado = pare.** Mostre os arquivos e linhas que o gitleaks apontou. Peça para rotacionar a credencial e remover do repositório. Nunca use `--no-secret-scan` para contornar.
3. **Índice é código-fonte.** Só libere um repositório para um token de quem já pode ler o repositório.
4. **Troca de modelo de embedding = reindexação completa** (`--full`). Dimensão diferente de 768 exige banco novo.
5. **Confirme antes** de reindexar com `--full` um repositório grande: em CPU, a base inicial leva cerca de 1 segundo por chunk.

## Passo a passo

1. Confira o repositório:
   ```bash
   git -C <caminho> rev-parse --verify main
   git -C <caminho> ls-files | wc -l
   ```
2. Indexe (primeira vez cria a base; depois só o que mudou):
   ```bash
   cd "$RAG_HOME"
   ./scripts/index.sh <caminho-ou-url> <nome>                 # compose
   ./scripts/index.sh <caminho-ou-url> <nome> --target k8s    # kubernetes
   ```
   Saída esperada na última linha: `<nome>: full|incremental|unchanged em <sha>: N arquivos, M chunks gravados, ...`.
3. Libere o acesso, se for um time novo:
   ```bash
   uv run rag token --name <time> --repos <nome>[,<outro>]
   ```
   Entregue o token ao time por canal seguro. Acrescente a linha JSON impressa em `"clients"` de `$RAG_HOME/acl.json`. Recarregue:
   - compose: `docker compose restart rag-mcp`
   - k8s: `./scripts/up.sh` (recria o Secret `rag-acl` e reinicia o MCP)
4. Confira pelo MCP: chame a ferramenta `list_repos`. O repositório aparece com o commit indexado e a contagem de chunks.
5. Teste uma busca com `search_code` usando um termo de negócio do repositório.

## Incremento contínuo

- Rode o mesmo comando do passo 2 depois de cada merge na `main`.
- No cluster, para repositórios com URL: copie `k8s/base/indexer-cronjob.yaml`, ajuste URL e nome, tire o `suspend`. No CI: `kubectl -n rag create job --from=cronjob/<nome> idx-$(date +%s)`.

## Problemas comuns

| Sintoma | Causa | Ação |
| --- | --- | --- |
| `embedder indisponível` | Embedder fora do ar ou sem memória | `docker compose ps embedder` ou `kubectl -n rag get pods`; veja os logs |
| `outro indexador gravou ...` | Duas indexações do mesmo repositório ao mesmo tempo | Rode de novo; a segunda vê o estado novo |
| `segredo encontrado` | gitleaks achou credencial | Regra 2 |
| `porta 15432 não abriu` | port-forward falhou (k8s) | `kubectl -n rag get cluster rag-db` deve estar `Cluster in healthy state` |
