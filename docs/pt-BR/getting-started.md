# Primeiros passos

Este guia leva um projeto do zero até um agente buscando no índice. Reserve cerca de 30 minutos, mais o tempo da primeira indexação.

[English](../getting-started.md)

## O que você precisa

- [uv](https://docs.astral.sh/uv/getting-started/installation/) e git na sua máquina.
- Docker na máquina que roda o servidor. Para a maioria dos times, comece com uma máquina.
- Cerca de 6 GB de memória livre para o Docker. O embedder incluído usa até 3 GB ao indexar.

## 1. Instale o plugin

No Claude Code:

```text
/plugin install ragnar --marketplace pedrojr/ragnar
```

O Claude Code pede a URL e o token do MCP. Se ainda não tem servidor, mantenha a URL padrão e informe o token depois do passo 2.

Usa outro agente? Siga [Outros agentes](agents.md) e volte para cá.

## 2. Suba o servidor

Siga o [`example/README.pt-BR.md`](../../example/README.pt-BR.md) ou peça ao agente:

> Use a skill infra do Ragnar para subir o servidor.

No fim você tem:

- uma URL do MCP, por padrão `http://127.0.0.1:8080/mcp`;
- um token, `RAGNAR_MCP_TOKEN` em `example/.env`;
- um worker que acompanha os branches de `example/repos.toml`.

Informe o token em `/plugin`, nas opções do Ragnar.

## 3. Ragnarize o projeto

Dentro do seu projeto:

```text
/ragnar:ragnarize
```

A skill roda `ragnar init`, que:

1. detecta as linguagens e o branch padrão;
2. grava o `.ragnar/config.toml`, com todas as chaves que um repositório pode definir, comentadas;
3. cria `.rag/` com os modelos comentados de `.env` e `config.local.toml`;
4. adiciona um bloco gerenciado ao `.gitignore`, cobrindo `.rag/`.

Faça commit de `.ragnar/` e `.gitignore`. Eles pertencem ao projeto. Nunca faça commit de `.rag/`: ela guarda segredos e arquivos locais. Veja [Configuração](configuration.md).

## 4. Monte o primeiro índice

Se o worker já lista seu repositório, ele começa sozinho. Acompanhe com `docker compose logs -f indexer`.

Para indexar agora, a partir de um clone local:

```text
/ragnar:index
```

Em CPU, o embedder incluído leva cerca de um segundo por chunk, então um repositório grande demora. As execuções seguintes tratam só o que mudou.

Antes, o indexador procura segredos com o [gitleaks](https://github.com/gitleaks/gitleaks). Um achado interrompe a execução. Troque a credencial, remova-a do repositório e rode de novo.

## 5. Busque

Pergunte ao agente sobre o código. A skill `search` escolhe a ferramenta para cada pergunta: ferramentas locais para textos exatos e mudanças recentes, e o índice para conceitos e símbolos.

A skill entra sozinha quando se aplica. Para forçar um primeiro teste:

> Use a skill search do Ragnar: onde tratamos pagamentos que falharam?

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
