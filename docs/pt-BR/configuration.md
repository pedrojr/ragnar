# Configuração

O Ragnar lê configurações de arquivos TOML e variáveis de ambiente.

[English](../configuration.md)

## De onde vêm as configurações

As camadas seguintes têm prioridade:

1. Valores padrão.
2. O preset indicado em `project.preset`.
3. `.ragnar/config.toml`, versionado com o projeto. `RAGNAR_CONFIG` pode apontar para outro arquivo.
4. `.rag/config.local.toml`. O git ignora a pasta `.rag/`, então use esse arquivo para ajustes pessoais.
5. Variáveis de ambiente `RAGNAR__SECAO__CHAVE`, como `RAGNAR__RETRIEVAL__MODE=vector`.
6. `--set secao.chave=valor`, nos comandos que aceitam.

Uma chave desconhecida ou um valor inválido interrompe o programa com uma mensagem que cita a configuração.

## Quem controla o quê

O projeto e o servidor são donos de configurações diferentes.

| Escopo | Arquivo | Vale para |
| --- | --- | --- |
| Projeto | `.ragnar/config.toml` no repositório | `[index]` e `project.languages` |
| Servidor | `example/server.toml` na infraestrutura | Todo o resto |

O worker lê o `.ragnar/config.toml` do repositório no commit acompanhado e usa só esses dois escopos. Um repositório não muda endpoints nem desliga a varredura de segredos, então um commit malicioso não desvia seu código nem esconde uma credencial.

A CLI aplica a mesma regra na sua máquina. Ao ler o `.ragnar/config.toml` da pasta do projeto, ela ignora `url`, `api_key_env` e `provider` em todas as seções, além de `index.secret_scan` e `index.secret_action`, e mostra um aviso com os nomes. Um repositório clonado não consegue apontar seus embeddings para outro servidor. Coloque essas chaves no `.rag/config.local.toml`, em variáveis de ambiente ou num arquivo indicado por `RAGNAR_CONFIG`, que é lido por inteiro. Se o git rastreia o `.rag/config.local.toml`, o arquivo também veio com o clone, e a CLI aplica a ele a mesma regra. Se o git rastreia o `.rag/.env`, a CLI não o carrega e mostra um aviso.

## Segredos

Segredos nunca vão nesses arquivos. Cada seção indica, em `api_key_env`, a variável de ambiente que guarda a chave.

Na máquina de quem desenvolve, coloque essas variáveis em `.rag/.env`. A CLI carrega esse arquivo ao iniciar e nunca substitui uma variável que já existe. Os comandos `mcp`, `embedder` e `embed-preload` nunca o leem.

## Embeddings e resumos

Duas etapas tornam o código pesquisável. Uma está sempre ligada. A outra é opcional.

| | Embeddings | Resumos |
| --- | --- | --- |
| Padrão | Ligados, sempre | Desligados |
| O que faz | Transforma cada chunk de código em um vetor. A busca compara o vetor da pergunta com o dos chunks, então acha código pelo sentido | Um modelo de chat escreve uma descrição curta de cada arquivo de código. A descrição é buscada como um chunk extra do tipo `summary` |
| Quando roda | Ao indexar, para todo chunk, e ao buscar, para a pergunta | Só ao indexar |
| Modelo | `jinaai/jina-embeddings-v2-base-code`, 768 dimensões, incluído e executado em CPU | Ollama, Claude Haiku ou qualquer modelo de chat compatível com a API da OpenAI, à sua escolha |
| Custo | Tempo de CPU, cerca de um segundo por chunk. Sem tarifa com o embedder incluído | Uma chamada de chat por arquivo de 30 linhas ou mais, enviando até 12.000 caracteres e recebendo até 300 tokens. Fica em cache pelo conteúdo do arquivo, então só arquivos novos ou alterados custam uma chamada |
| Melhora | Achar código a partir de uma pergunta em linguagem natural | Perguntas em termos de negócio que o código nunca usa, como em código legado com nomes ruins |
| Configuração | `[embedding]` | `[summaries]` e `[llm]` |
| Chave | `RAGNAR_EMBEDDING_API_KEY`, só para uma API hospedada | `RAGNAR_LLM_API_KEY`, para a Anthropic ou outra API hospedada |
| Envia código para | Ninguém, com o embedder incluído. O provedor, com uma API hospedada | Ninguém, com o Ollama. O provedor, com uma API hospedada |

Para ligar os resumos com o Ollama rodando no host, em `example/server.toml`:

```toml
[summaries]
enabled = true
provider = "ollama"                           # ou "anthropic", para o Claude Haiku
url = "http://host.docker.internal:11434/v1"  # omita para "anthropic"
exclude = ["vendor/"]                         # pastas que ficam sem resumo
```

Depois reinicie com `docker compose restart indexer`. A execução seguinte reindexa o repositório, e os chunks sem mudança mantêm seus vetores. Para desligar os resumos, use `enabled = false`. Resumos são uma configuração do servidor: um repositório não os liga pelo próprio `.ragnar/config.toml`, mas pode deixar pastas de fora com `index.summary_exclude`. Ligá-los sem um modelo interrompe o programa com um erro.

[Resumos de arquivos](summaries.md) mostra o passo a passo com o Ollama e com o Haiku, com um comando de prévia e uma tabela de problemas comuns.

`[summaries]` pode ter provedor e modelo próprios. As chaves que ficam vazias vêm de `[llm]`, que atende multi-query, HyDE e recuperação contextual. Veja [Técnicas de recuperação](retrieval.md).

No Compose, defina as chaves em `example/.env`. O `compose.yaml` repassa `RAGNAR_EMBEDDING_API_KEY` a todos os serviços da aplicação, `RAGNAR_LLM_API_KEY` ao `indexer` (resumos e recuperação contextual) e ao `ragnar-mcp` (estratégias de consulta), e `RAGNAR_RERANK_API_KEY` ao `ragnar-mcp`. No Kubernetes, o `scripts/up.sh` copia as mesmas três chaves do `example/.env` para o Secret `ragnar-model-keys`, que o servidor MCP e o worker leem.

## Presets

| Preset | Use para | Muda |
| --- | --- | --- |
| `economy` | Menor custo em tokens por busca. Agentes podem compensar com mais greps | 3 resultados, 1.000 tokens, 2 chunks por arquivo |
| `balanced` | Padrão | Nada |
| `precise` | Perguntas difíceis, custo maior | 10 resultados, 8.000 tokens, 100 candidatos, `ef_search` 200, chunks vizinhos, diversidade |

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
| `include` | `[]` | Globs extras, indexados por janelas de linhas. Uma `/` no fim indica um diretório |
| `exclude` | `[]` | Globs a ignorar, como `"legacy/*"`. Uma `/` no fim indica um diretório, como `"vendor/"` |
| `max_file_kb` | `2048` | Arquivos maiores são ignorados, e a execução informa quais |
| `chunk_budget` | `1800` | Caracteres sem espaço por chunk |
| `window_lines` | `80` | Linhas por chunk, para arquivos sem gramática |
| `window_overlap` | `10` | Linhas repetidas entre janelas |
| `index_tests` | `true` | Indexa arquivos de teste |
| `index_docs` | `true` | Indexa Markdown |
| `index_agent_notes` | `false` | Indexa instruções escritas para agentes (`AGENTS.md`, `CLAUDE.md`, regras do Copilot e do Cursor). O agente já as lê |
| `secret_scan` | `true` | Roda o gitleaks. Só no escopo do servidor |
| `secret_action` | `"fail"` | Ao achar um segredo: `fail` para a execução, `skip` deixa esses arquivos de fora. Só no escopo do servidor |
| `legacy_encoding` | `"cp1252"` | Alternativa para arquivos que não são UTF-8 |
| `summary_exclude` | `[]` | Diretórios (terminados em `/`) e globs que ficam sem [resumo de arquivo](summaries.md#deixar-pastas-de-fora). Soma-se a `summaries.exclude` |

Mudar qualquer uma delas, exceto `secret_scan` e `secret_action`, reindexa o repositório inteiro na execução seguinte. Chunks com o mesmo texto mantêm seus vetores, então a reindexação só paga pelo que é novo.

## `[embedding]`

Qualquer endpoint compatível com `/v1/embeddings` funciona: o embedder incluído, Ollama, vLLM, LiteLLM ou um provedor hospedado. Veja [Embeddings e resumos](#embeddings-e-resumos).

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

A `url` padrão usa a porta 8001, onde a infraestrutura de exemplo publica o embedder. O `ragnar embedder` escuta na 8000 por padrão, então rode com `--port 8001` ou ajuste a `url`.

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
| `literal_weight` | `1.0` | Peso da lista de literais, usada quando a pergunta cita um texto entre aspas: chunks cujas strings têm todas as palavras citadas. `0` desliga |
| `graph_weight` | `0.0` | Peso da lista do grafo: o que os melhores resultados chamam e quem os chama. `0` desliga |
| `graph_seeds` | `5` | Melhores resultados cujos vizinhos no grafo entram na lista do grafo |
| `doc_weight` | `0.5` | Escala a pontuação fundida dos resultados de documentação. Abaixo de 1, o código ganha de documentos de visão geral que citam todos os módulos |
| `rank_normalization` | `1` | Como o ranking lexical desconta chunks longos: `1` divide por 1 + log(tamanho), `2` pelo tamanho. Aceita qualquer flag do `ts_rank` do PostgreSQL: 0, 1, 2, 4, 8, 16 ou 32. `2` ajudou um monorepo Delphi grande e atrapalhou projetos menores |
| `ef_search` | `100` | Largura da busca HNSW |
| `max_chunks_per_file` | `3` | Limite por arquivo. `0` desliga |
| `diversity` | `0.0` | `0` é só relevância, `1` é só novidade |
| `neighbor_chunks` | `0` | Chunks vizinhos adicionados a cada resultado |
| `max_context_tokens` | `2000` | Orçamento estimado de tokens. `0` desliga |
| `reference_hits` | `8` | Resultados que não couberam, devolvidos só com path e símbolo |
| `excerpts` | `true` | Depois do primeiro, cada resultado mantém só as linhas numeradas em volta das palavras da pergunta e diz quais linhas deixou de fora |

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

## `[summaries]`

| Chave | Padrão | Significado |
| --- | --- | --- |
| `enabled` | `false` | Escreve um resumo de cada arquivo de código ao indexar. Veja [Resumos de arquivos](summaries.md) |
| `provider` | `""` | `ollama`, `anthropic` ou `openai`. Vazio: usa `[llm]` |
| `url` | `""` | URL base. Vazio: o padrão do provedor, ou `llm.url` quando `provider` está vazio |
| `model` | `""` | Nome do modelo. Vazio: o padrão do provedor, ou `llm.model` quando `provider` está vazio |
| `api_key_env` | `""` | Variável com a chave. Vazio: `llm.api_key_env` |
| `language` | `""` | Idioma dos resumos, como `"Brazilian Portuguese"`. Vazio: o modelo escolhe |
| `exclude` | `[]` | Diretórios (terminados em `/`) e globs que ficam sem resumo |
| `min_lines` | `30` | Arquivos menores ficam sem resumo |
| `max_chars` | `12000` | Texto do arquivo enviado ao modelo |
| `concurrency` | `4` | Chamadas paralelas ao modelo |
| `timeout` | `300.0` | Segundos por chamada ao modelo. `0`: usa `llm.timeout` |

## `[llm]`

`provider` escolhe a API. `openai` funciona com qualquer endpoint `/chat/completions`, `ollama` usa a API `/v1` compatível do Ollama, e `anthropic` usa a Messages API da Anthropic.

| Provedor | `url` padrão | `model` padrão |
| --- | --- | --- |
| `openai` | nenhuma | nenhum |
| `ollama` | `http://127.0.0.1:11434/v1` | `qwen3:8b` |
| `anthropic` | `https://api.anthropic.com/v1` | `claude-haiku-5-5` |

| Chave | Padrão | Significado |
| --- | --- | --- |
| `provider` | `"openai"` | `openai`, `ollama` ou `anthropic` |
| `url` | `""` | URL base. Vazio: o padrão do provedor |
| `model` | `""` | Nome do modelo. Vazio: o padrão do provedor |
| `api_key_env` | `"RAGNAR_LLM_API_KEY"` | Variável com a chave |
| `temperature` | `0.0` | Temperatura |
| `max_tokens` | `300` | Limite da resposta |
| `timeout` | `60.0` | Segundos |

## Variáveis de execução

Elas configuram um processo, não a recuperação.

| Variável | Usada por | Significado |
| --- | --- | --- |
| `RAGNAR_DSN_WRITE` | Indexador, migrações, `ragnar smoke` (checagens das barreiras de escrita) | Conexão com o primário |
| `RAGNAR_DSN_READ` | Servidor MCP, `ragnar eval`, `ragnar search`, `ragnar smoke` (checagens das barreiras de escrita) | Conexão com as réplicas |
| `RAGNAR_ACL_FILE` | Servidor MCP | Caminho do `acl.json` |
| `RAGNAR_CONFIG` | Todos | Caminho do arquivo de configuração |
| `RAGNAR_POOL_MAX` | Servidor MCP | Conexões com o banco, padrão `10` |
| `RAGNAR_POOL_MAX_LIFETIME` | Servidor MCP | Vida da conexão em segundos, padrão `120` |
| `RAGNAR_STATEMENT_TIMEOUT` | Servidor MCP | Consulta mais longa, como duração do PostgreSQL, padrão `30s` |
| `RAGNAR_EMBED_THREADS` | Embedder | Threads de CPU |
| `RAGNAR_EMBED_MAX_TOKENS` | Embedder | Corte da entrada, padrão `512` |
| `RAGNAR_EMBED_BATCH` | Embedder | Lote interno, padrão `8` |
| `FASTEMBED_CACHE_PATH` | Embedder, `ragnar embed-preload` | Pasta dos arquivos do modelo de embedding |
| `RAGNAR_MCP_TOKEN` | `ragnar smoke`, `ragnar load` | Token usado pelas verificações e pelo teste de carga |
| `RAGNAR_MCP_TOKEN_RESTRICTED` | `ragnar smoke` | Um token sem acesso ao repositório, para testar a ACL |
| `RAGNAR_VENV` | `bin/ragnar` | Pasta do ambiente da CLI do plugin |

Os comandos da CLI também leem essas variáveis do `.rag/.env`. Veja [Linha de comando](cli.md).

### Infraestrutura de exemplo

Os arquivos de `example/` leem estas variáveis do `.env`. O `ragnar init-env` grava as senhas e os tokens.

| Variável | Usada por | Significado |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | Compose | Senha do papel administrador `postgres` |
| `REPLICATION_PASSWORD` | Compose | Senha do papel com que a réplica se conecta |
| `RAGNAR_WRITER_PASSWORD` | Compose, Kubernetes | Senha do `ragnar_writer`, usada no `RAGNAR_DSN_WRITE` |
| `RAGNAR_READER_PASSWORD` | Compose, Kubernetes | Senha do `ragnar_reader`, usada no `RAGNAR_DSN_READ` |
| `RAGNAR_EMBED_MEMORY` | Compose | Limite de memória do contêiner do embedder, padrão `3g` |
| `RAGNAR_EMBED_THREADS` | Compose | Threads de CPU do contêiner do embedder, padrão `4` |
| `RAGNAR_LOCAL_EMBED_MODEL` | Compose, `k8s/base/embed.env` | Modelo embutido no embedder incluído, padrão `jinaai/jina-embeddings-v2-base-code`. Separado de `RAGNAR__EMBEDDING__MODEL`, para um modelo hospedado não derrubar o contêiner |
| `RAGNAR_SYNC_INTERVAL` | Compose | Segundos entre as passagens do worker de indexação, padrão `60` |
| `K8S_CONTEXT` | `scripts/up.sh` | Contexto do kubectl que o script exige, padrão `docker-desktop` |
