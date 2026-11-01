# Arquitetura

[English](../architecture.md)

## Componentes

| Componente | Código | Papel |
| --- | --- | --- |
| Plugin | `.claude-plugin/`, `.mcp.json`, `skills/`, `hooks/`, `bin/` | Conecta o agente e guia o fluxo |
| CLI | `src/ragnar/cli.py` | Todas as operações, para pessoas e skills |
| Indexador | `chunker.py`, `languages.py`, `delphi.py`, `indexer.py` | Transforma um commit em chunks, símbolos e vetores |
| Worker | `sync.py` | Acompanha branches e chama o indexador |
| Recuperação | `search.py`, `llm.py` | O pipeline de busca |
| Servidor MCP | `mcp_server.py`, `acl.py` | Acesso somente leitura para agentes |
| Embedder | `embed_server.py`, `embedder.py` | Embedder de CPU incluído, e cliente para qualquer endpoint |
| Avaliação | `evaluation.py` | Mede a recuperação contra o grep |
| Preparação do projeto | `project.py` | `ragnar init` e `ragnar doctor` |
| Configuração | `config.py` | Camadas, validação e fingerprint |

## Fluxo de dados

Indexação:

1. O worker espelha o repositório e resolve o branch acompanhado para um commit.
2. Compara o commit com `index_state`. Modelo novo, configuração de chunking nova ou histórico reescrito levam a uma execução completa. Do contrário, lê os arquivos alterados pelo `git diff`.
3. Lê o conteúdo dos arquivos no commit com `git cat-file`.
4. Normaliza encodings e procura segredos. Um achado interrompe a execução.
5. Monta chunks e símbolos e calcula embeddings fora de qualquer transação.
6. Numa transação curta, trava o repositório, confirma que ninguém gravou, troca os chunks e registra o commit.

Busca:

1. O agente chama uma ferramenta MCP com um token.
2. O servidor confere o token e resolve quais repositórios ele pode ler.
3. O pipeline de [Técnicas de recuperação](retrieval.md) roda numa réplica.
4. O resultado volta entre delimitadores, e uma linha de auditoria é gravada.

## Decisões de projeto

**Ler commits, nunca o diretório de trabalho.** O índice reflete um estado conhecido, e segredos ou rascunhos não commitados nunca entram. Mudanças locais ficam com a skill `search`.

**Um escritor, muitos leitores.** Só o indexador grava, e só no primário. O servidor MCP conecta com um papel que só lê, nas réplicas. Um bug ou uma injeção de prompt no caminho da busca não altera o índice.

**Credenciais separadas por serviço.** O processo MCP nunca vê a senha de escrita.

**O índice é dado derivado.** O git é a fonte da verdade, então recuperar é reindexar. Por isso o exemplo não trata de backup.

**Um repositório não configura o servidor.** Ele ajusta o próprio chunking, mas não alcança endpoints, chaves nem a varredura de segredos. Veja [Configuração](configuration.md).

**Polling antes de webhooks.** Polling não precisa de rede de entrada, então funciona atrás de firewall. Quem quer indexar a cada push pode usar os modelos de CI.

**Chamadas de modelo são opcionais.** O pipeline padrão só precisa de um embedder, então o custo fica previsível. O resto é opcional e medido com `ragnar eval`.

**Só interfaces padrão.** MCP para os agentes. `/v1/embeddings`, `/rerank` e `/chat/completions` para os modelos. Nenhuma parte depende de um fornecedor.

## Modelo de dados

| Tabela | Conteúdo |
| --- | --- |
| `code_chunk` | Uma linha por chunk: localização, tipo, texto, tsvector, embedding |
| `code_symbol` | Uma linha por definição: nome, nome qualificado, assinatura |
| `index_state` | Por repositório: commit indexado, modelo, fingerprint das configurações |
| `replication_probe` | Mede o atraso de replicação no teste de fumaça |

## Organização do repositório

```text
.claude-plugin/   manifestos do plugin e do marketplace
.mcp.json         conexão MCP, preenchida pelas opções do plugin
bin/              o comando ragnar, para os agentes
hooks/            hook de SessionStart
skills/           fluxos para agentes
src/ragnar/       pacote Python
tests/            testes
docs/             documentação (docs/pt-BR/ tem a tradução)
example/          infraestrutura de referência
```
