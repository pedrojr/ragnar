# Servidor MCP

O servidor MCP do Ragnar dá aos agentes acesso somente leitura ao índice, por Streamable HTTP. Ele não guarda sessão, então qualquer réplica atende qualquer requisição.

[English](../mcp.md)

## Ferramentas

### `find_symbol`

Acha definições pelo nome. Casa o início do nome, ignora maiúsculas e lista nomes exatos primeiro. Devolve path, linhas, nome qualificado e assinatura, sem o corpo, então é a chamada mais barata.

| Parâmetro | Tipo | Significado |
| --- | --- | --- |
| `name` | texto | Nome ou início do nome |
| `repo` | texto, opcional | Limita a um repositório |
| `k` | inteiro, opcional | Máximo de resultados, padrão 20, até 50 |

### `search_code`

Roda o [pipeline de recuperação](retrieval.md) configurado sobre código, testes e docs. Aceita linguagem natural, nomes de símbolos ou trechos de código.

| Parâmetro | Tipo | Significado |
| --- | --- | --- |
| `query` | texto | A busca |
| `repo` | texto, opcional | Limita a um repositório |
| `path_prefix` | texto, opcional | Limita a uma pasta |
| `kind` | texto, opcional | `code`, `test` ou `doc` |
| `k` | inteiro, opcional | Máximo de trechos, até 20. O padrão é `retrieval.default_k` |

Os trechos chegam dentro de `<retrieved_context>`, com um aviso para o modelo tratá-los como dados. Tags de fechamento no conteúdo do repositório são escapadas, então um arquivo não consegue sair do bloco.

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
