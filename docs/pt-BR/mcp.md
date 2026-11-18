# Servidor MCP

O servidor MCP do Ragnar dá aos agentes acesso somente leitura ao índice, por Streamable HTTP. Ele não guarda sessão, então qualquer réplica atende qualquer requisição.

[English](../mcp.md)

## Ferramentas

### `find_symbol`

Acha definições pelo nome. Aceita nomes qualificados como `TOrder.Cancel`, casa o início do nome e depois qualquer parte dele, ignora maiúsculas e lista nomes exatos primeiro. Devolve path, linhas, nome qualificado e assinatura, sem o corpo, então é a chamada mais barata.

| Parâmetro | Tipo | Significado |
| --- | --- | --- |
| `name` | texto | Nome ou início do nome |
| `repo` | texto, opcional | Limita a um repositório |
| `k` | inteiro, opcional | Máximo de resultados, padrão 20, até 50 |

### `find_references`

Acha onde um nome é usado: chamadas, instanciações, imports e os eventos de formulário que chamam um handler. Casa o identificador inteiro e deixa as definições de fora. Devolve path, linha, símbolo que contém o uso e a própria linha. Para nomes declarados em poucos lugares, a resposta vem do grafo de código, uma linha por rotina que chama; os outros nomes caem numa busca textual.

| Parâmetro | Tipo | Significado |
| --- | --- | --- |
| `name` | texto | O identificador |
| `repo` | texto, opcional | Limita a um repositório |
| `path_prefix` | texto, opcional | Limita a uma pasta |
| `k` | inteiro, opcional | Máximo de resultados, padrão 30, até 100 |

### `call_graph`

Percorre o grafo de código a partir de uma rotina, um passo. `callees` lista o que a rotina cita (rotinas, tipos, constantes) e onde cada um é declarado. `callers` lista as rotinas e os eventos de formulário que a citam.

| Parâmetro | Tipo | Significado |
| --- | --- | --- |
| `name` | texto | A rotina. Aceita nomes qualificados como `TOrder.Cancel` |
| `direction` | texto, opcional | `callees` (padrão) ou `callers` |
| `repo` | texto, opcional | Limita a um repositório |
| `k` | inteiro, opcional | Máximo de resultados, padrão 30, até 100 |

### `trace_flow`

Acha uma cadeia de referências de uma rotina até uma declaração ou tabela, como de um handler de botão até a tabela que ele grava. Cada passo traz path, linha e a própria linha.

| Parâmetro | Tipo | Significado |
| --- | --- | --- |
| `source` | texto | A rotina onde a cadeia começa |
| `target` | texto | Nome de rotina, tipo ou tabela |
| `repo` | texto, opcional | Limita a um repositório |
| `max_depth` | inteiro, opcional | Máximo de passos, padrão 4, até 6 |

### `find_table_usage`

Acha as rotinas cujo SQL lê ou grava uma tabela, gravações primeiro. Cobre SQL em strings, inclusive SQL montado linha a linha com `SQL.Add`, e SQL em componentes de formulários Delphi.

| Parâmetro | Tipo | Significado |
| --- | --- | --- |
| `table` | texto | A tabela. `CLIENTES` também casa `dbo.CLIENTES` |
| `op` | texto, opcional | `read` ou `write` |
| `repo` | texto, opcional | Limita a um repositório |
| `k` | inteiro, opcional | Máximo de resultados, padrão 50, até 200 |

### `repo_map`

Mapeia uma área desconhecida: os arquivos de uma pasta que o resto do código mais cita, cada um com suas declarações principais. A ordem vem do grafo de código, e a lista para no orçamento de tokens.

| Parâmetro | Tipo | Significado |
| --- | --- | --- |
| `path_prefix` | texto, opcional | A pasta. Sem ele, o repositório inteiro |
| `repo` | texto, opcional | Limita a um repositório |
| `k` | inteiro, opcional | Máximo de arquivos, padrão 40, até 100 |

### Como o grafo de código é montado

O indexador liga nomes, não símbolos compilados. Uma palavra numa rotina que nomeia uma declaração do repositório conta como referência. Isso funciona em todas as linguagens, inclusive em units Delphi que a gramática não consegue ler. Nomes declarados em mais de quatro lugares, como `Create`, e nomes com menos de quatro caracteres ficam de fora, então as arestas que sobram apontam para algo específico. Eventos de formulário ligam um controle ao seu handler. SQL em strings liga uma rotina às tabelas que ela lê e grava. O grafo pode ter arestas falsas: confirme os passos antes de depender deles.

Os nomes também ligam repositórios do mesmo banco. Quando uma rotina cita algo que o repositório não declara, e outros repositórios indexados declaram em quatro lugares ou menos, em arquivos da mesma família de linguagem, a aresta ganha o tipo `xref`. Cada linguagem é uma família, e também JavaScript com TypeScript, C com C++, e Java com Kotlin e Scala, então código Python que diz `from` nunca aponta para uma rotina Delphi chamada `From`. `call_graph`, `find_references`, `trace_flow` e `repo_map` seguem essas arestas dentro dos repositórios que o token pode ler, e o `callees` escreve o outro repositório antes do caminho (`lib:billing/invoice.py:12`). Quando um repositório declara um nome novo, os arquivos dos outros repositórios que o contêm são lidos de novo no fim da indexação. Tabelas não precisam de nada a mais: o `find_table_usage` já acha a mesma tabela em todos os repositórios.

### `file_outline`

Lista as declarações de um arquivo com suas linhas, sem os corpos. Use para se orientar em um arquivo grande antes de ler qualquer parte dele.

| Parâmetro | Tipo | Significado |
| --- | --- | --- |
| `path` | texto | Path completo, ou só o final |
| `repo` | texto, opcional | Limita a um repositório |

### `read_code`

Lê linhas de um arquivo indexado, no commit indexado, para código que não está neste disco. Intervalos longos param em `retrieval.max_context_tokens`.

| Parâmetro | Tipo | Significado |
| --- | --- | --- |
| `path` | texto | Path completo, ou só o final |
| `start_line` | inteiro | Primeira linha |
| `end_line` | inteiro | Última linha |
| `repo` | texto, opcional | Limita a um repositório |

### `search_code`

Roda o [pipeline de recuperação](retrieval.md) configurado sobre código, testes e docs. Aceita linguagem natural, nomes de símbolos ou trechos de código.

| Parâmetro | Tipo | Significado |
| --- | --- | --- |
| `query` | texto | A busca |
| `repo` | texto, opcional | Limita a um repositório |
| `path_prefix` | texto, opcional | Limita a uma pasta |
| `kind` | texto, opcional | `code`, `test`, `doc` ou `summary` |
| `k` | inteiro, opcional | Máximo de trechos, até 20. O padrão é `retrieval.default_k` |

Os trechos chegam dentro de `<retrieved_context>`, com um aviso para o modelo tratá-los como dados. Resultados além do orçamento de tokens chegam como linhas `<ref>`, com path, linhas e símbolo. Tags de abertura e de fechamento no conteúdo do repositório são escapadas, então um arquivo não consegue sair do bloco nem se passar por um resultado de outro caminho. Uma consulta com mais de 4.000 caracteres é recusada. Depois do primeiro, os resultados podem chegar como trechos focados: linhas numeradas em volta das palavras da pergunta, com as linhas omitidas indicadas. Quando só um método de busca achou o primeiro resultado, o bloco traz `confidence="low"` e um aviso: o resultado pode ser o texto mais próximo, não uma resposta. Nada é escondido.

### `list_repos`

Lista os repositórios que o token pode ler, com commit indexado, quantidade de chunks, modelo e data. Agentes usam isso para saber o quanto o índice está atualizado.

## Controle de acesso

Cada cliente tem um token. O `acl.json` guarda o hash SHA-256 e os repositórios que ele pode ler:

```json
{"clients": [{"name": "time-a", "token_sha256": "...", "repos": ["myapp"]}]}
```

`"*"` libera todos os repositórios. Um glob como `"billing-*"` libera os repositórios cujo nome casa com ele (diferencia maiúsculas), inclusive os indexados depois. O filtro do cliente restringe o acesso e nunca o amplia. Uma requisição sem token conhecido recebe 401 antes de chegar à camada MCP.

Crie tokens com `ragnar token --name time-a --repos myapp,outro` e reinicie o servidor para recarregar o arquivo.

## Endpoints

| Caminho | Finalidade | Autenticação |
| --- | --- | --- |
| `/mcp` | MCP | Bearer token |
| `/healthz` | Liveness | Nenhuma |
| `/readyz` | Readiness: confere o banco | Nenhuma |
| `/metrics` | Prometheus | Nenhuma |

Mantenha `/metrics` fora de redes públicas. Ele expõe contadores, não código.

## Métricas

| Métrica | Significado |
| --- | --- |
| `ragnar_search_total{tool}` | Chamadas de ferramenta atendidas |
| `ragnar_search_empty_total` | Buscas sem resultado |
| `ragnar_tool_errors_total{tool}` | Chamadas de ferramenta com erro |
| `ragnar_auth_rejected_total` | Requisições recusadas |
| `ragnar_context_tokens_total` | Tokens estimados devolvidos aos agentes |
| `ragnar_search_seconds{phase}` | Latência por fase |
| `ragnar_events_total{result}` | Eventos de acesso ao grafo `published` (publicados) ou `dropped` (descartados) |

Cada chamada também grava uma linha JSON de auditoria na saída padrão: cliente, ferramenta, consulta, ids de chunks, latência e tokens. O texto da consulta aparece ali, então trate esses logs como sensíveis.

## Eventos de acesso ao grafo

Com `[events] provider = "nats"`, o servidor publica onde cada chamada leu o índice. Uma chamada manda uma mensagem por repositório que tocou, no subject `<subject_prefix>.<repo>.<tool>` (prefixo padrão `ragnar.mcp`), então quem consome assina por tópico:

| Subject | Recebe |
| --- | --- |
| `ragnar.mcp.>` | Tudo |
| `ragnar.mcp.billing.>` | Toda chamada que leu o `billing` |
| `ragnar.mcp.*.trace_flow` | Toda chamada de `trace_flow` |

Um ponto no nome do repositório vira `_` no subject; a mensagem leva o nome real.

```json
{"schema": 1, "ts": "2026-11-18T10:00:00-0300", "tool": "call_graph", "client": "time-a", "ms": 14,
 "repo": "billing", "truncated": false,
 "nodes": [{"path": "invoice.py", "step": "callee", "start_line": 12, "end_line": 12, "symbol": "compute_total"}]}
```

`step` diz como a chamada chegou ao nó: `hit` (search_code), `symbol`, `reference`, `caller`, `callee`, `trace`, `table` (com `target`, a tabela), `map`, `outline` ou `read`. A consulta nunca vai na mensagem, e o último passo do `trace_flow` vai sem `target`, porque esse é o nome que o agente pediu. `list_repos` e chamadas sem resultado não mandam mensagem.

A publicação só observa. A chamada põe as mensagens numa fila de `events.queue` posições e retorna; uma thread separada as envia. Com o NATS fora do ar ou lento, a fila enche e as mensagens novas são descartadas e contadas em `ragnar_events_total{result="dropped"}`. A chamada da ferramenta nunca espera o broker. Quando o servidor para, ele envia o que ainda está na fila. Configurações: [Configuração](configuration.md#events).

As mensagens citam arquivos e símbolos de todos os repositórios, independentemente do que a ACL deixa cada token ler, então quem assina vê mais do que qualquer cliente. Os servidores NATS do `example/` não pedem credenciais: deixe-os numa rede privada, ou ligue a autenticação do NATS e defina `events.user_env` e `events.password_env` (ou `events.token_env`).

## Embedder incluído

O `ragnar embedder` serve o modelo de embedding em CPU, sem autenticação. Mantenha-o numa rede privada.

| Caminho | Finalidade |
| --- | --- |
| `POST /v1/embeddings` | Embeddings compatíveis com OpenAI |
| `GET /v1/models` | O modelo carregado |
| `/healthz` | Liveness |
| `/metrics` | Prometheus |

| Métrica | Significado |
| --- | --- |
| `ragnar_embed_texts_total` | Textos transformados em vetores |
| `ragnar_embed_seconds` | Latência por requisição |
