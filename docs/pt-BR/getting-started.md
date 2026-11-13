# Primeiros passos

Este guia leva um projeto do zero até um agente buscando no índice. Reserve cerca de 30 minutos, mais o tempo da primeira indexação.

[English](../getting-started.md)

## Requisitos

| Necessidade | Onde | Observações |
| --- | --- | --- |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | Sua máquina | Roda o comando `ragnar`. Ele baixa o Python 3.11 ou mais novo se você não tiver |
| git | Sua máquina | O Ragnar lê commits, nunca a árvore de trabalho |
| Docker com Compose | A máquina do servidor | Roda o Postgres com pgvector, o embedder, o servidor MCP e o worker. Comece com uma máquina |
| Cerca de 6 GB de memória livre | A máquina do servidor | O embedder incluído usa até 3 GB ao indexar |
| [gitleaks](https://github.com/gitleaks/gitleaks) | Sua máquina, só para indexar a partir de um clone local | A imagem do worker já o inclui. Sem ele, o `ragnar index` roda o gitleaks pelo Docker |

Opcional:

- Uma API de embeddings no lugar do embedder incluído. Ela exige uma chave e recebe seu código-fonte.
- Um endpoint de modelo de chat (`[llm]`) para resumos de arquivos, multi-query, HyDE e recuperação contextual. Tudo isso vem desligado.
- Um endpoint de reranker.
- `uv sync --extra embed`, que instala o fastembed, a biblioteca por trás do embedder incluído. A imagem Docker já o traz. Você só precisa dele para rodar `ragnar embedder` fora do Docker.

Os embeddings estão sempre ligados, e os resumos são opcionais. [Embeddings e resumos](configuration.md#embeddings-e-resumos) explica a diferença.

## 1. Suba o servidor

Siga o [`example/README.pt-BR.md`](../../example/README.pt-BR.md) ou peça ao agente:

> Use a skill infra do Ragnar para subir o servidor.

Quando deu certo:

- `docker compose ps` mostra `db-primary`, `db-replica` e `embedder` saudáveis, `ragnar-mcp` e `indexer` no ar, e `migrate` encerrado com código 0.
- `docker compose logs -f indexer` termina a primeira montagem com uma linha como `myapp: full at 3f2a9c1b7d10: 412 files, 1830 chunks, 1902.4s`.
- `./scripts/smoke.sh myapp` passa em todas as verificações.

Agora você tem uma URL do MCP (por padrão `http://127.0.0.1:8080/mcp`), um token (`RAGNAR_MCP_TOKEN` em `example/.env`) e um worker que acompanha os branches de `example/repos.toml`.

## 2. Conecte o plugin

No Claude Code:

```text
/plugin install ragnar --marketplace pedrojr/ragnar
```

O Claude Code pede a URL e o token do MCP. Informe os valores do passo 1. Você pode mudá-los depois em `/plugin`, nas opções do Ragnar.

Usa outro agente? Siga [Outros agentes](agents.md) e volte para cá.

Quando deu certo, pedir ao agente que chame `list_repos` devolve seu repositório e o commit indexado.

## 3. Ragnarize o projeto

Dentro do seu projeto:

```text
/ragnar:ragnarize
```

A skill roda `ragnar init`, que detecta as linguagens e o branch padrão, cria os arquivos abaixo e adiciona um bloco gerenciado ao `.gitignore`.

| Caminho | Commitar? | Conteúdo |
| --- | --- | --- |
| `.ragnar/config.toml` | Sim | Nome do projeto, branch, preset, linguagens e as chaves de `[index]` que um repositório pode definir, comentadas com os valores padrão |
| `.ragnar/eval/golden.jsonl` | Sim | Perguntas de avaliação. Criado por `/ragnar:evaluate` |
| `.gitignore` | Sim | O bloco gerenciado que ignora `.rag/` |
| `AGENTS.md`, e `CLAUDE.md` se existir | Sim | Um bloco gerenciado com as regras de busca: quando usar o índice e quando usar grep. Pule com `--no-agent-notes` |
| `.rag/.env` | Nunca | Segredos do comando `ragnar`, comentados: `RAGNAR_DSN_READ`, `RAGNAR_MCP_TOKEN` e as chaves de API de embedding, rerank e LLM |
| `.rag/config.local.toml` | Nunca | Seus ajustes pessoais |
| `.rag/eval/` | Nunca | Relatórios de avaliação |

O comando carrega `.rag/.env` ao iniciar e nunca substitui uma variável já definida no seu shell. O servidor nunca o lê. O Compose lê as chaves do servidor em `example/.env`.

Quando deu certo, o `ragnar init` imprime `.ragnar/config.toml: written`, `.rag/.env: written`, `.rag/config.local.toml: written`, `.gitignore: updated` e `AGENTS.md: written`. Rodar de novo é seguro: ele mantém os arquivos existentes e reescreve só os blocos gerenciados.

No Claude Code, os hooks do plugin repetem a regra sem gastar um turno. No início da sessão, e de novo depois de `/clear` ou de uma compactação, o agente fica sabendo que o projeto está indexado e que a skill `search` escolhe a ferramenta. No primeiro `Grep`, `Glob` ou `grep` no shell da sessão, ele recebe um lembrete de uma linha com as ferramentas do índice. Os hooks só rodam em projetos com `.ragnar/config.toml` e nunca bloqueiam uma chamada.

Faça commit de `.ragnar/` e `.gitignore`. O worker lê o `.ragnar/config.toml` do branch acompanhado, então mudanças em `[index]` valem quando chegam a esse branch. Veja [Configuração](configuration.md).

## 4. Monte o primeiro índice

Se o worker já lista seu repositório, ele começa sozinho. Acompanhe com `docker compose logs -f indexer`.

Para indexar agora, a partir de um clone local:

```text
/ragnar:index
```

Em CPU, o embedder incluído leva cerca de um segundo por chunk, então um repositório grande demora. As execuções seguintes tratam só o que mudou.

Antes, o indexador procura segredos com o gitleaks. Um achado interrompe a execução. Troque a credencial, remova-a do repositório e rode de novo.

Quando deu certo, o log mostra uma linha como `myapp: full at <sha>`, e o `list_repos` mostra o mesmo commit.

## 5. Busque

Pergunte ao agente sobre o código. A skill `search` escolhe a ferramenta para cada pergunta: ferramentas locais para textos exatos e mudanças recentes, e o índice para conceitos e símbolos.

Para forçar um primeiro teste:

> Use a skill search do Ragnar: onde tratamos pagamentos que falharam?

Quando deu certo, a resposta cita locais `path:linha` do seu repositório.

## 6. Meça

Não presuma que o índice ajuda. Meça no seu histórico:

```text
/ragnar:evaluate
```

Depois ajuste as configurações com evidência:

```text
/ragnar:tune
```

Veja [Avaliação](evaluation.md).

## 7. Mantenha saudável

O worker mantém o índice atualizado. Quando algo parecer errado:

```text
/ragnar:maintain
```

Avalie de novo depois de grandes refatorações ou de uma troca de modelo.

## Levando para o time

1. Crie um token por time: `ragnar token --name time-a --repos myapp`.
2. Adicione a entrada impressa em `example/acl.json` e reinicie o `ragnar-mcp`.
3. Compartilhe a URL do MCP. Envie cada token por um canal seguro.
4. Leia o [SECURITY.pt-BR.md](../../SECURITY.pt-BR.md) antes de expor o servidor na rede.
