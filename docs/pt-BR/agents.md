# Outros agentes

O servidor do Ragnar fala MCP, um protocolo aberto, então qualquer cliente MCP com suporte a HTTP consegue buscar no índice. O Claude Code recebe o plugin completo. Nos demais, você conecta o servidor e adiciona as regras de busca.

[English](../agents.md)

Os formatos de configuração dos clientes mudam com frequência. Se um bloco abaixo falhar, confira a documentação atual de MCP do seu cliente.

## Instale a CLI

As skills e a preparação do projeto usam o comando `ragnar`:

```bash
uv tool install git+https://github.com/pedrojr/ragnar
ragnar init --agents-md
```

`--agents-md` adiciona as regras de busca ao `AGENTS.md`, que muitos agentes leem.

## Conecte o servidor

Guarde o token numa variável de ambiente: `export RAGNAR_MCP_TOKEN=...`.

**Cursor**, em `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "ragnar": {
      "url": "http://127.0.0.1:8080/mcp",
      "headers": { "Authorization": "Bearer ${env:RAGNAR_MCP_TOKEN}" }
    }
  }
}
```

**VS Code**, em `.vscode/mcp.json`:

```json
{
  "servers": {
    "ragnar": {
      "type": "http",
      "url": "http://127.0.0.1:8080/mcp",
      "headers": { "Authorization": "Bearer ${env:RAGNAR_MCP_TOKEN}" }
    }
  }
}
```

**Codex CLI**, em `~/.codex/config.toml`:

```toml
[mcp_servers.ragnar]
url = "http://127.0.0.1:8080/mcp"
bearer_token_env_var = "RAGNAR_MCP_TOKEN"
```

**Outros clientes:** use Streamable HTTP, a URL acima e um cabeçalho `Authorization: Bearer <token>`.

## Use as skills

Os arquivos em [`skills/`](../../skills/) seguem o formato Agent Skills: um `SKILL.md` com nome e descrição. Se seu cliente aceita esse formato, copie as pastas para onde ele carrega skills. Se não aceita, aponte o agente para o arquivo quando precisar, por exemplo: "siga skills/evaluate/SKILL.md".

A skill `search` é a mais importante. Sem ela, agentes tendem a abusar do índice ou do grep.

## Outros modelos

O modelo do agente não importa para o Ragnar. As escolhas de modelo do servidor são independentes:

| Parte | Funciona com |
| --- | --- |
| Embeddings | Qualquer endpoint `/v1/embeddings` |
| Reranking | Qualquer endpoint `/rerank` no formato Jina, Cohere ou TEI |
| Expansão de consulta e recuperação contextual | Qualquer endpoint `/chat/completions` compatível com OpenAI |

Veja [Configuração](configuration.md).
