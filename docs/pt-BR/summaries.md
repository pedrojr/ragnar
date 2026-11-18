# Resumos de arquivos

[English](../summaries.md)

Um resumo de arquivo é um parágrafo curto que um modelo de chat escreve sobre um arquivo de código, com as palavras que a equipe usa para falar do negócio. Para uma unit Delphi cheia de `btnTrancarClick` e `TB_MATR`, o resumo pode dizer "registra o trancamento da matrícula de um aluno no semestre". O Ragnar indexa esse parágrafo como mais um chunk, do tipo `summary`. Assim, uma pergunta feita em termos de negócio encontra o arquivo mesmo quando o código nunca usa essas palavras.

Os resumos vêm desligados (`summaries.enabled = false`). Esta página mostra como ligá-los com o Ollama na sua máquina ou com o Claude Haiku pela API da Anthropic, como deixar pastas de fora e como conferir o resultado.

## Como funciona

1. Ao indexar, o Ragnar seleciona todo arquivo de código com 30 linhas ou mais. Testes, documentação e as pastas excluídas ficam de fora.
2. Ele envia ao modelo os primeiros 12.000 caracteres de cada arquivo, junto com os nomes que o arquivo declara e os nomes a que ele se refere.
3. O modelo responde com duas ou três frases.
4. O Ragnar guarda a resposta em um cache indexado pelo conteúdo do arquivo e pelo modelo. Um arquivo que não mudou nunca custa uma segunda chamada, nem em uma reindexação completa. Uma execução incremental resume só os arquivos dos seus commits, do mesmo jeito que só gera vetores para os chunks deles.
5. O resumo vira vetor e é buscado como qualquer outro chunk. `read_code`, `file_outline` e as ferramentas de linha o ignoram.

## Escolha o provedor

| | Ollama | Anthropic (Haiku) |
| --- | --- | --- |
| Onde o modelo roda | Na sua máquina ou servidor | Na API da Anthropic |
| O código sai da sua rede | Não | Sim, para a Anthropic |
| Custo | Hardware e tempo. Um modelo de 7 a 8B em GPU leva alguns segundos por arquivo; em CPU, bem mais | Por token. Cada arquivo envia até 12.000 caracteres e recebe até 300 tokens |
| Chave | Nenhuma | Uma chave da API da Anthropic |
| Modelo padrão | `qwen3:8b` | `claude-haiku-5-5` |
| URL padrão | `http://127.0.0.1:11434/v1` | `https://api.anthropic.com/v1` |

Defina `summaries.provider` e o Ragnar preenche a URL e o modelo. Qualquer outro endpoint compatível com a OpenAI (vLLM, LiteLLM, uma API hospedada) também funciona com `provider = "openai"`, mas aí você precisa definir `url` e `model`.

## Passo a passo com o Ollama

1. Instale o Ollama a partir de <https://ollama.com> e baixe o modelo:

   ```bash
   ollama pull qwen3:8b
   ```

2. Confira se ele responde:

   ```bash
   curl http://127.0.0.1:11434/v1/models
   ```

3. Ligue os resumos. Para um teste local, em `.rag/config.local.toml`:

   ```toml
   [summaries]
   enabled = true
   provider = "ollama"
   language = "Brazilian Portuguese"   # opcional: o idioma dos resumos
   ```

   No Compose, coloque o mesmo bloco em `example/server.toml` e aponte a URL para o host, porque `127.0.0.1` dentro do contêiner é o próprio contêiner:

   ```toml
   [summaries]
   enabled = true
   provider = "ollama"
   url = "http://host.docker.internal:11434/v1"
   ```

   O serviço `indexer` mapeia `host.docker.internal` para o host, inclusive no Linux. Se o Ollama rodar como um serviço do próprio Compose, use `http://ollama:11434/v1`. O `scripts/index.sh` roda no host com o mesmo `server.toml`, e no Linux com Docker Engine o host não resolve `host.docker.internal`: adicione o nome ao `/etc/hosts` como `127.0.0.1`, ou deixe a indexação para o worker.

4. Veja uma prévia de alguns arquivos antes de indexar. Veja [Prévia antes de indexar](#prévia-antes-de-indexar).

5. Indexe. Veja [Aplicar e conferir](#aplicar-e-conferir).

Para usar outro modelo, baixe-o e defina, por exemplo, `model = "qwen2.5-coder:7b"`. Modelos de raciocínio imprimem um bloco `<think>` antes da resposta; o Ragnar o remove.

## Passo a passo com o Claude Haiku

1. Crie uma chave de API em <https://console.anthropic.com>.

2. Guarde a chave em uma variável de ambiente. Nunca a escreva em um arquivo TOML. Na máquina de quem desenvolve, em `.rag/.env`:

   ```bash
   RAGNAR_LLM_API_KEY=sk-ant-...
   ```

   No Compose, coloque a mesma linha em `example/.env`. O `compose.yaml` repassa `RAGNAR_LLM_API_KEY` ao `indexer`.

3. Ligue os resumos, em `.rag/config.local.toml` ou em `example/server.toml`:

   ```toml
   [summaries]
   enabled = true
   provider = "anthropic"
   language = "Brazilian Portuguese"   # opcional
   ```

4. Veja a prévia e indexe, como nas seções seguintes.

Se o `[llm]` já usa `RAGNAR_LLM_API_KEY` para outro provedor, dê aos resumos uma variável própria com `api_key_env = "RAGNAR_SUMMARIES_API_KEY"` e repasse essa variável ao `indexer` no `compose.yaml`.

## Deixar pastas de fora

Valem duas listas. Um arquivo que casa com qualquer uma delas fica sem resumo, mas o código dele continua indexado.

| Configuração | Quem define | Onde |
| --- | --- | --- |
| `summaries.exclude` | Quem administra o servidor | `example/server.toml` ou `.rag/config.local.toml` |
| `index.summary_exclude` | Cada repositório | O `.ragnar/config.toml` versionado no repositório |

Cada item é um caminho relativo à raiz do repositório:

- Um diretório, escrito com `/` no final: `"legacy/"` cobre tudo dentro de `legacy`.
- Um glob: `"*/generated/*"` ou `"src/reports/*.pas"`. O `*` também atravessa `/`.

```toml
# example/server.toml
[summaries]
enabled = true
provider = "ollama"
exclude = ["vendor/", "third_party/"]
```

```toml
# .ragnar/config.toml, no repositório
[index]
summary_exclude = ["generated/", "src/old_reports/"]
```

Deixe de fora o código sobre o qual ninguém pergunta em termos de negócio: bibliotecas de terceiros, código gerado, migrações, módulos mortos. Cada arquivo excluído economiza uma chamada ao modelo.

Um repositório só pode acrescentar exclusões. Ele não liga os resumos nem escolhe provedor, modelo ou chave: isso fica com o servidor.

## Prévia antes de indexar

`ragnar summarize` envia arquivos locais ao modelo configurado e imprime os resumos. Não grava nada no índice e não precisa de banco de dados.

```bash
ragnar summarize src/Secretaria/uTrancamento.pas src/Mensalidades/uMulta.pas
```

```text
provider: ollama, model: qwen3:8b, url: http://127.0.0.1:11434/v1
## src/Secretaria/uTrancamento.pas
Registra o trancamento da matrícula de um aluno ...
```

Rode o comando na raiz do repositório, para que os caminhos batam com as listas de exclusão. Para testar uma configuração sem editar arquivo, use `--set`:

```bash
ragnar summarize src/Secretaria/uTrancamento.pas --set summaries.provider=anthropic --set 'summaries.language="English"'
```

## Aplicar e conferir

1. No Compose, reinicie o indexador: `docker compose restart indexer`. Localmente, rode `ragnar index . myapp`.
2. A primeira execução depois da mudança reindexa o repositório inteiro. Os chunks cujo texto não mudou mantêm os vetores, então só os resumos custam tempo. O log mostra `summaries: N files to the model, M cached`.
3. Faça uma pergunta em termos de negócio:

   ```bash
   ragnar search "trancamento de matrícula" --repo myapp
   ```

   Os resultados de resumo trazem o símbolo `(summary)`. Para receber só resumos, passe `--kind summary`, ou `kind = "summary"` ao `search_code` pelo MCP.

Mudar o modelo, `min_lines`, `language`, `summaries.exclude` ou `index.summary_exclude` provoca uma reindexação completa na execução seguinte. Os resumos que já estão no cache para o mesmo modelo e idioma são reaproveitados.

## Configurações

Todas as chaves de `[summaries]`. Chaves vazias usam o valor de `[llm]`; veja [Configuração](configuration.md#summaries).

| Chave | Padrão | Significado |
| --- | --- | --- |
| `enabled` | `false` | Escreve um resumo de cada arquivo de código ao indexar |
| `provider` | `""` | `ollama`, `anthropic` ou `openai`. Vazio: usa `[llm]` |
| `url` | `""` | URL base. Vazio: o padrão do provedor, ou `llm.url` quando `provider` está vazio |
| `model` | `""` | Nome do modelo. Vazio: o padrão do provedor, ou `llm.model` quando `provider` está vazio |
| `api_key_env` | `""` | Variável com a chave. Vazio: `llm.api_key_env` (`RAGNAR_LLM_API_KEY`) |
| `language` | `""` | Idioma dos resumos, por extenso: `"Brazilian Portuguese"`. Vazio: o modelo escolhe |
| `exclude` | `[]` | Diretórios (terminados em `/`) e globs que ficam sem resumo |
| `min_lines` | `30` | Arquivos menores ficam sem resumo |
| `max_chars` | `12000` | Texto do arquivo enviado ao modelo |
| `concurrency` | `4` | Chamadas paralelas ao modelo. Reduza para o Ollama em uma máquina pequena |
| `timeout` | `300.0` | Segundos por chamada ao modelo. `0`: usa `llm.timeout`, que protege as buscas e fica curto |

`temperature` e `max_tokens` vêm de `[llm]`. Uma chamada que estoura o tempo deixa sem resumo só aquele arquivo, e a execução continua. O arquivo ganha o resumo quando mudar, ou no próximo `ragnar index --full`, que pede ao modelo só os arquivos que faltam no cache. Depois de três chamadas seguidas com falha, o Ragnar considera o modelo fora do ar e para a execução. O índice fica no commit anterior, os resumos já feitos ficam no cache, e a próxima execução continua dali.

Quando `summaries.provider` está definido, a seção fica independente: `url` e `model` nunca vêm de `[llm]`. Assim os resumos podem rodar no Haiku enquanto o multi-query roda no Ollama, ou o contrário.

## Limpar o cache

O cache guarda todo resumo que um modelo já escreveu, inclusive os de arquivos que mudaram depois, de outro modelo ou de outro idioma. O `ragnar gc` remove os que nenhum chunk de resumo usa e que têm mais de 7 dias (`--older-than DIAS`). `--dry-run` só conta. Veja [Linha de comando](cli.md#ragnar-gc).

## Problemas comuns

| Mensagem | Causa | Solução |
| --- | --- | --- |
| `summaries.enabled requires a chat model` | Sem provedor e sem `[llm]` | Defina `summaries.provider` |
| `the anthropic provider needs an API key: set RAGNAR_LLM_API_KEY` | A variável está vazia neste processo | Coloque-a em `.rag/.env` ou em `example/.env` |
| `chat model unavailable at http://127.0.0.1:11434/v1 (ollama): ... refused` | O Ollama não está rodando, ou o indexador roda em um contêiner | Inicie o Ollama, ou use `url = "http://host.docker.internal:11434/v1"` |
| `chat model unavailable ... 404` | O modelo não foi baixado, ou o nome está errado | `ollama pull <modelo>`, ou confira `model` |
| `chat model unavailable ... timed out` | O modelo é lento nesta máquina | Aumente `summaries.timeout` ou reduza `summaries.concurrency` |
