# Linha de comando

Todos os comandos do `ragnar` e as suas opções. `ragnar <comando> --help` mostra a mesma lista.

[English](../cli.md)

Os comandos, exceto `mcp`, `embedder` e `embed-preload`, leem antes o `.rag/.env` da pasta atual. Uma variável já definida no ambiente vale mais que o arquivo. As variáveis estão em [Configuração](configuration.md#variáveis-de-execução).

`--set SEÇÃO.CHAVE=VALOR` troca uma configuração só naquela execução, como `--set retrieval.default_k=10`. Repita a opção para mais configurações. Os comandos que a aceitam estão indicados abaixo.

## Projeto

### `ragnar init`

Ragnariza um projeto: grava o `.ragnar/config.toml`, a `.rag/`, o bloco gerenciado do `.gitignore` e as regras de busca para os agentes.

| Opção | Padrão | Significado |
| --- | --- | --- |
| `--dir` | `.` | Pasta do projeto |
| `--preset` | `balanced` | `economy`, `balanced` ou `precise` |
| `--branch` | Branch padrão do repositório | Branch que o índice acompanha |
| `--name` | Nome da pasta | Nome do repositório no índice |
| `--no-agent-notes` | Desligado | Não mexe no `AGENTS.md` nem no `CLAUDE.md` |
| `--force` | Desligado | Regrava um `.ragnar/config.toml` existente |

### `ragnar doctor`

Confere a saúde de um projeto ragnarizado.

| Opção | Padrão | Significado |
| --- | --- | --- |
| `--dir` | `.` | Pasta do projeto |
| `--indexed-commit` | Nenhum | Commit que o `list_repos` informa, para medir o atraso do índice |

### `ragnar summarize ARQUIVO...`

Mostra os resumos que o modelo configurado escreveria para arquivos locais. Não grava nada. Os caminhos são relativos à raiz do repositório. Aceita `--set`.

## Indexação

### `ragnar index ORIGEM REPO`

Cria ou atualiza o índice de um repositório. `ORIGEM` é um caminho local ou uma URL git, e `REPO` é o nome no índice. Precisa do `RAGNAR_DSN_WRITE`. Aceita `--set`.

| Opção | Padrão | Significado |
| --- | --- | --- |
| `--ref` | `project.branch` | Branch ou commit |
| `--full` | Desligado | Reindexa tudo |
| `--no-secret-scan` | Desligado | Pula o gitleaks. Só para testes |

### `ragnar sync`

Indexa os branches listados no `repos.toml`. Precisa do `RAGNAR_DSN_WRITE`. Aceita `--set`.

| Opção | Padrão | Significado |
| --- | --- | --- |
| `--config` | `repos.toml` | Caminho da lista de repositórios |
| `--repo` | Todos | Só este repositório. Precisa estar na lista |
| `--watch` | Desligado | Continua consultando |
| `--interval` | `60` | Segundos entre as passagens com `--watch` |

### `ragnar migrate`

Aplica as migrações pendentes no primário. Precisa do `RAGNAR_DSN_WRITE`.

| Opção | Significado |
| --- | --- |
| `--resize-embeddings` | Apaga o índice e o grafo de código, e aplica o `embedding.dim`. Use depois de trocar o modelo |

## Busca e medição

### `ragnar search CONSULTA`

Busca direto no banco de leitura, para depuração. Mostra os resultados como o `search_code` mostraria, e o tempo de cada fase na saída de erro. Precisa do `RAGNAR_DSN_READ`. Aceita `--set`.

| Opção | Padrão | Significado |
| --- | --- | --- |
| `--repo` | Obrigatório | Nome do repositório no índice |
| `-k` | `retrieval.default_k` | Número de resultados |
| `--path-prefix` | Nenhum | Só caminhos sob este prefixo |
| `--kind` | Todos | `code`, `test`, `doc` ou `summary` |

### `ragnar eval golden REPO SNAPSHOT`

Cria perguntas a partir dos commits depois do `SNAPSHOT`, no repositório local `REPO`. Veja [Avaliação](evaluation.md).

| Opção | Padrão | Significado |
| --- | --- | --- |
| `--branch` | `main` | Branch cujos commits viram perguntas |
| `--out` | `.ragnar/eval/golden.jsonl` | Arquivo de saída |

### `ragnar eval run REPO SNAPSHOT NOME_NO_INDICE`

Compara o grep, a configuração atual e as suas variantes nas perguntas. `NOME_NO_INDICE` é o nome do snapshot no índice. Precisa do `RAGNAR_DSN_READ`. Aceita `--set`.

| Opção | Padrão | Significado |
| --- | --- | --- |
| `--golden` | `.ragnar/eval/golden.jsonl` | Arquivo de perguntas |
| `--variant` | Nenhuma | `NOME:SEÇÃO.CHAVE=VALOR[,...]`, outra configuração para pontuar. Pode repetir |
| `--out` | `.rag/eval/report.json` | Arquivo do relatório, em JSON |

### `ragnar stats LOG`

Relatório de uso a partir das linhas de auditoria do servidor MCP. `LOG` é um arquivo com a saída do servidor, ou `-` para a entrada padrão. Veja [Servidor MCP](mcp.md#métricas).

| Opção | Significado |
| --- | --- |
| `--labels ARQUIVO` | Grava as perguntas que um `read_code` posterior respondeu, para o `ragnar eval run` |

## Servidores

### `ragnar mcp` e `ragnar embedder`

Sobem o servidor MCP e o embedder incluído.

| Opção | Padrão | Significado |
| --- | --- | --- |
| `--host` | `0.0.0.0` | Endereço em que escuta. Use `127.0.0.1` para ficar só na máquina |
| `--port` | `8080` no `mcp`, `8000` no `embedder` | Porta |

### `ragnar embed-preload`

Baixa o modelo de embedding para o `FASTEMBED_CACHE_PATH`. O build da imagem roda este comando.

## Infraestrutura de exemplo

### `ragnar init-env`

Cria o `.env` com senhas e tokens aleatórios, além do `acl.json` e do `repos.toml`. Os clientes adicionados à mão no `acl.json` ficam.

| Opção | Padrão | Significado |
| --- | --- | --- |
| `--dir` | `.` | Pasta de destino |
| `--force` | Desligado | Recria um `.env` existente |

### `ragnar token`

Cria um token e mostra a entrada dele para o `acl.json`. O token aparece uma vez e não é guardado.

| Opção | Significado |
| --- | --- |
| `--name` | Nome do cliente. Obrigatório |
| `--repos` | Repositórios separados por vírgula, ou `*` para todos. Obrigatório |

### `ragnar smoke`

Checagem de ponta a ponta pelo MCP, mais as barreiras de escrita. Lê o `RAGNAR_MCP_TOKEN` e, quando definidos, o `RAGNAR_MCP_TOKEN_RESTRICTED`, o `RAGNAR_DSN_WRITE` e o `RAGNAR_DSN_READ`.

| Opção | Padrão | Significado |
| --- | --- | --- |
| `--url` | `http://127.0.0.1:8080/mcp` | Endpoint MCP |
| `--repo` | Obrigatório | Repositório em que busca |
| `--replica-host` | Nenhum | Um host de réplica, para conferir que ele recusa escritas |

### `ragnar load`

Carga de buscas pelo MCP. Lê o `RAGNAR_MCP_TOKEN`.

| Opção | Padrão | Significado |
| --- | --- | --- |
| `--url` | `http://127.0.0.1:8080/mcp` | Endpoint MCP |
| `--concurrency` | `8` | Clientes em paralelo |
| `--duration` | `120` | Segundos |
| `--rate` | `0` | Buscas por segundo no total. `0` roda o mais rápido possível |
| `--queries` | Lista embutida | Arquivo com uma consulta por linha |
