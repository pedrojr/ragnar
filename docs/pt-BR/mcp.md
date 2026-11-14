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

`"*"` libera todos os repositórios. O filtro do cliente restringe o acesso e nunca o amplia. Uma requisição sem token conhecido recebe 401 antes de chegar à camada MCP.

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

Cada chamada também grava uma linha JSON de auditoria na saída padrão: cliente, ferramenta, consulta, ids de chunks, latência e tokens. O texto da consulta aparece ali, então trate esses logs como sensíveis.

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
