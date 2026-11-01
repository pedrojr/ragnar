---
name: rag-avaliar
description: Mede se o RAG de código ajuda, comparando a busca híbrida com o baseline sem RAG (git grep) num golden set tirado do histórico do git. Calcula Recall@10, MRR e o intervalo de confiança pareado, e aplica a regra de decisão definida antes de rodar. Use para "avaliar o RAG", "o RAG está ajudando?", "comparar com e sem RAG", "medir Recall", "/rag-avaliar", ou antes de trocar modelo, chunker ou parâmetros.
compatibility: Requer o exemplo do RAG (RAG_HOME), uv, git e o banco do RAG acessível (compose no ar ou port-forward do k8s).
metadata:
  kit: code-rag
  version: "0.1.0"
---

# Avaliar o RAG (com e sem)

Implementa a seção 8.1 do guia `rag-codigo-guia.md`: cada commit vira uma pergunta, e os arquivos que ele alterou são a resposta esperada.

## Entradas

- Repositório (caminho local) e nome no índice. Se faltar, pergunte.
- Data de corte T (padrão: 60 dias atrás). Perguntas vêm só de commits depois de T.
- Regra de decisão, **definida antes de rodar**. Padrão: adotar se o ganho de Recall@10 tiver IC95% inteiro acima de zero.

## Regras

1. **Sem vazamento.** Indexe um snapshot do repositório em T, com outro nome (`<nome>-snap`). Nunca avalie no índice atual: a resposta já estaria nele.
2. **Mesmas perguntas para os dois sistemas.** Comparação pareada.
3. **Amostra pequena não decide.** Com menos de 30 perguntas, reporte os números como indicativos. Sugira completar com perguntas escritas por pessoas do time, no formato de `eval/golden.jsonl`.
4. **Não altere o índice de produção.** O snapshot usa nome próprio; apague-o no fim se o usuário quiser.

## Passo a passo

1. Snapshot em T e índice dele:
   ```bash
   cd "$RAG_HOME"
   SNAP=$(git -C <repo> rev-list -1 --before="<T>" main)
   ./scripts/index.sh <repo> <nome>-snap --ref "$SNAP"            # acrescente --target k8s no cluster
   ```
2. Golden set a partir dos commits depois do snapshot:
   ```bash
   uv run python eval/build_golden.py <repo> "$SNAP" > eval/golden.jsonl
   ```
3. Comparação (leitura com `rag_reader`):
   ```bash
   set -a; . ./.env; set +a
   export RAG_DSN_READ="postgresql://rag_reader:${RAG_READER_PASSWORD}@127.0.0.1:15432/rag"
   export EMBED_URL=http://127.0.0.1:8001/v1
   uv run python eval/eval_retrieval.py <repo> "$SNAP" <nome>-snap eval/golden.jsonl
   ```
   No k8s, abra antes os túneis: `kubectl -n rag port-forward svc/rag-db-ro 15432:5432` e `svc/rag-embedder 8001:8000`.
4. Relate em tabela: perguntas, Recall@10 e MRR de cada sistema, ganho com IC95%, e a decisão pela regra.

## Leitura do resultado

- IC95% contém zero: sem evidência de ganho. Não adote só por isso; veja se o golden set é pequeno.
- Use como portão ao mudar chunker, modelo ou parâmetros: o Recall@10 não pode cair mais de 2 pontos.
- Recuperação boa não garante tarefa resolvida. O teste que decide adoção é o de tarefas (seção 8.3 do guia).
