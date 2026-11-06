# Configuração

O Ragnar lê configurações de arquivos TOML e variáveis de ambiente.

[English](../configuration.md)

## De onde vêm as configurações

As camadas seguintes têm prioridade:

1. Valores padrão.
2. O preset indicado em `project.preset`.
3. `ragnar.toml`, versionado com o projeto. `RAGNAR_CONFIG` pode apontar para outro arquivo.
4. `ragnar.local.toml`, ao lado dele. O git ignora esse arquivo, então use-o para ajustes pessoais.
5. Variáveis de ambiente `RAGNAR__SECAO__CHAVE`, como `RAGNAR__RETRIEVAL__MODE=vector`.
6. `--set secao.chave=valor`, nos comandos que aceitam.

Uma chave desconhecida ou um valor inválido interrompe o programa com uma mensagem que cita a configuração.

## Quem controla o quê

O projeto e o servidor são donos de configurações diferentes.

| Escopo | Arquivo | Vale para |
| --- | --- | --- |
| Projeto | `ragnar.toml` no repositório | `[index]` e `project.languages` |
| Servidor | `example/server.toml` na infraestrutura | Todo o resto |

O worker lê o `ragnar.toml` do repositório no commit acompanhado e usa só esses dois escopos. Um repositório não muda endpoints nem desliga a varredura de segredos, então um commit malicioso não desvia seu código nem esconde uma credencial.

## Segredos

Segredos nunca vão nesses arquivos. Cada seção indica, em `api_key_env`, a variável de ambiente que guarda a chave.

## Presets

| Preset | Use para | Muda |
| --- | --- | --- |
| `economy` | Menor custo em tokens por busca. Agentes podem compensar com mais greps | 3 resultados, 1.000 tokens, 2 chunks por arquivo |
| `balanced` | Padrão | Nada |
| `precise` | Perguntas difíceis, custo maior | 10 resultados, 8.000 tokens, 100 candidatos, chunks vizinhos, diversidade |

## `[project]`

| Chave | Padrão | Significado |
| --- | --- | --- |
| `name` | `""` | Nome do repositório no índice |
| `branch` | `"main"` | Branch que o índice acompanha |
| `preset` | `"balanced"` | `economy`, `balanced` ou `precise` |
| `languages` | `[]` | Linguagens a indexar. Vazio significa todas |

## `[index]`

| Chave | Padrão | Significado |
| --- | --- | --- |
| `include` | `[]` | Globs extras, indexados por janelas de linhas |
| `exclude` | `[]` | Globs a ignorar |
| `max_file_kb` | `2048` | Arquivos maiores são ignorados, e a execução informa quais |
| `chunk_budget` | `1800` | Caracteres sem espaço por chunk |
| `window_lines` | `80` | Linhas por chunk, para arquivos sem gramática |
| `window_overlap` | `10` | Linhas repetidas entre janelas |
| `index_tests` | `true` | Indexa arquivos de teste |
| `index_docs` | `true` | Indexa Markdown |
| `secret_scan` | `true` | Roda o gitleaks. Só no escopo do servidor |
| `secret_action` | `"fail"` | Ao achar um segredo: `fail` para a execução, `skip` deixa esses arquivos de fora. Só no escopo do servidor |
| `legacy_encoding` | `"cp1252"` | Alternativa para arquivos que não são UTF-8 |

Mudar qualquer uma delas, exceto `secret_scan` e `secret_action`, reindexa o repositório inteiro na execução seguinte. Chunks com o mesmo texto mantêm seus vetores, então a reindexação só paga pelo que é novo.

## `[embedding]`

Qualquer endpoint compatível com `/v1/embeddings` funciona: o embedder incluído, Ollama, vLLM, LiteLLM ou um provedor hospedado.

| Chave | Padrão | Significado |
| --- | --- | --- |
| `provider` | `"http"` | `http`, ou `fake` para testes |
| `url` | `"http://127.0.0.1:8001/v1"` | URL base |
| `model` | `"jinaai/jina-embeddings-v2-base-code"` | Nome do modelo |
| `api_key_env` | `"RAGNAR_EMBEDDING_API_KEY"` | Variável com a chave |
| `dim` | `768` | Dimensão do vetor |
| `send_dimensions` | `false` | Envia `dimensions`, para modelos com vetores encurtados |
| `doc_prefix` | `""` | Texto antes de cada documento |
| `query_prefix` | `""` | Texto antes de cada consulta |
| `batch` | `16` | Textos por requisição |
| `concurrency` | `2` | Requisições paralelas ao indexar |

Para trocar o modelo, ajuste `model` e `dim`. A execução seguinte reindexa tudo. Outra dimensão também exige `ragnar migrate --resize-embeddings`, que limpa o índice. O índice HNSW aceita até 2.000 dimensões: para modelos maiores, use `send_dimensions` para encurtar os vetores.

Um provedor hospedado recebe seu código-fonte. Escolha com isso em mente.

## `[retrieval]`

| Chave | Padrão | Significado |
| --- | --- | --- |
| `mode` | `"hybrid"` | `hybrid`, `vector` ou `lexical` |
| `default_k` | `5` | Resultados quando quem chama não define `k` |
| `candidates` | `50` | Resultados por lista, antes da fusão |
| `rrf_k` | `30` | Constante de suavização do RRF |
| `vector_weight` | `1.0` | Peso da lista vetorial |
| `lexical_weight` | `1.0` | Peso da lista lexical |
| `symbol_weight` | `1.0` | Peso de símbolos exatos. `0` desliga |
| `file_weight` | `2.0` | Peso da lista por arquivo inteiro. `0` desliga |
| `ef_search` | `100` | Largura da busca HNSW |
| `max_chunks_per_file` | `3` | Limite por arquivo. `0` desliga |
| `diversity` | `0.0` | `0` é só relevância, `1` é só novidade |
| `neighbor_chunks` | `0` | Chunks vizinhos adicionados a cada resultado |
| `max_context_tokens` | `2000` | Orçamento estimado de tokens. `0` desliga |
| `reference_hits` | `8` | Resultados que não couberam, devolvidos só com path e símbolo |

## `[rerank]`

Qualquer endpoint no formato de `/rerank` usado por Jina, Cohere e TEI funciona.

| Chave | Padrão | Significado |
| --- | --- | --- |
| `provider` | `"none"` | `none` ou `http` |
| `url` | `""` | URL completa do endpoint |
| `model` | `""` | Nome do modelo |
| `api_key_env` | `"RAGNAR_RERANK_API_KEY"` | Variável com a chave |
| `candidates` | `30` | Resultados enviados ao reranker |

## `[query]`

| Chave | Padrão | Significado |
| --- | --- | --- |
| `strategy` | `"none"` | `none`, `multi_query` ou `hyde`. As duas últimas exigem `[llm]` |
| `variants` | `3` | Reescritas no `multi_query` |

## `[contextual]`

| Chave | Padrão | Significado |
| --- | --- | --- |
| `enabled` | `false` | Escreve uma nota de contexto por chunk ao indexar. Exige `[llm]` |
| `max_file_chars` | `20000` | Texto do arquivo enviado com cada chunk |
| `concurrency` | `4` | Chamadas paralelas ao modelo |

## `[llm]`

Qualquer endpoint `/chat/completions` compatível com OpenAI funciona.

| Chave | Padrão | Significado |
| --- | --- | --- |
| `url` | `""` | URL base |
| `model` | `""` | Nome do modelo |
| `api_key_env` | `"RAGNAR_LLM_API_KEY"` | Variável com a chave |
| `temperature` | `0.0` | Temperatura |
| `max_tokens` | `300` | Limite da resposta |
| `timeout` | `60.0` | Segundos |

## Variáveis de execução

Elas configuram um processo, não a recuperação.

| Variável | Usada por | Significado |
| --- | --- | --- |
| `RAGNAR_DSN_WRITE` | Indexador, migrações | Conexão com o primário |
| `RAGNAR_DSN_READ` | Servidor MCP, avaliação | Conexão com as réplicas |
| `RAGNAR_ACL_FILE` | Servidor MCP | Caminho do `acl.json` |
| `RAGNAR_CONFIG` | Todos | Caminho do arquivo de configuração |
| `RAGNAR_POOL_MAX` | Servidor MCP | Conexões com o banco, padrão `10` |
| `RAGNAR_POOL_MAX_LIFETIME` | Servidor MCP | Vida da conexão em segundos, padrão `120` |
| `RAGNAR_EMBED_THREADS` | Embedder | Threads de CPU |
| `RAGNAR_EMBED_MAX_TOKENS` | Embedder | Corte da entrada, padrão `512` |
| `RAGNAR_EMBED_BATCH` | Embedder | Lote interno, padrão `8` |
| `RAGNAR_VENV` | `bin/ragnar` | Pasta do ambiente da CLI do plugin |
