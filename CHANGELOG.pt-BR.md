# Changelog

Todas as mudanças relevantes ficam registradas aqui. O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/), e as versões seguem o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

[English](CHANGELOG.md)

## [Unreleased]

### Adicionado

- Plugin para Claude Code na raiz do repositório, com as skills `ragnarize`, `search`, `index`, `evaluate`, `tune`, `maintain` e `infra`.
- Configuração por `.ragnar/config.toml`, com presets, validação e sobrescrita por camadas.
- Perfis de linguagem para Python, JavaScript, TypeScript, Java, Kotlin, C#, Go, Rust, C, C++, PHP, Ruby, Swift, Scala, Bash, SQL e Delphi.
- Suporte a Delphi: fontes em UTF-16 e CP-1252, símbolos de units e métodos, e leitura de formulários.
- Recuperação configurável: fusão ponderada, reforço por símbolo, reranking, multi-query, HyDE, recuperação contextual, diversidade, chunks vizinhos e orçamento de tokens.
- `ragnar init` e `ragnar doctor`, incluindo um bloco gerenciado no `.gitignore`.
- `ragnar sync` e um worker de indexação que acompanha branches dos repositórios.
- `ragnar eval`, com comparação de variantes e estimativa de tokens de contexto.
- Infraestrutura de referência em `example/`, com modelos de CI.
- Documentação em inglês e português do Brasil.
- Ferramentas MCP `find_references`, `file_outline` e `read_code`. `find_symbol` aceita nomes qualificados e casa qualquer parte do nome.
- Lista por arquivo inteiro na fusão (`retrieval.file_weight`) e referências para resultados além do orçamento de tokens (`retrieval.reference_hits`).
- `index.secret_action`, para deixar de fora os arquivos com achados em vez de parar a execução. Só no escopo do servidor.
- `benchmark/`: comparação de antes e depois em projetos open source fixados em Delphi, Java, C# e Go.
- Formulários Delphi binários (`TPF0`) são lidos como texto, e dados de imagem em formulários texto ficam fora dos chunks e do limite de tamanho.
- `llm.provider` e `summaries.provider` (`openai`, `ollama`, `anthropic`), com URLs e modelos padrão. O provedor `anthropic` chama a Messages API, para o Claude Haiku.
- `[summaries]` aceita `provider`, `url`, `model` e `api_key_env` próprios, com `[llm]` como alternativa, além de `language` para o idioma dos resumos.
- `summaries.exclude` no servidor e `index.summary_exclude` no repositório deixam pastas fora dos resumos de arquivos.
- `ragnar summarize`, para ver uma prévia dos resumos de arquivos locais com o modelo configurado.
- Guia [Resumos de arquivos](docs/pt-BR/summaries.md).
- `summaries.timeout` (300 segundos): os resumos rodam na indexação e não dividem mais o `llm.timeout` curto que protege as buscas.
- `ragnar search --kind`, o mesmo filtro do `search_code`.
- `exclude` por repositório no `repos.toml`.
- Um primeiro índice interrompido retoma do seu checkpoint de embeddings (migração 006).
- `RAGNAR_BENCH_SUITE` e `RAGNAR_BENCH_DSN` rodam o benchmark numa suíte privada, e `benchmark/summarize_suite.py` publica só os agregados dela.
- `RAGNAR_EMBED_THREADS` e `RAGNAR_EMBED_MEMORY` na infraestrutura de exemplo.
- `retrieval.rank_normalization`, o desconto por tamanho do ranking lexical, que antes era uma constante.
- Strings literais (mensagens, captions de formulário, SQL) ganham um campo lexical próprio e uma lista de literais na fusão (`retrieval.literal_weight`). A lista só roda quando a pergunta cita um texto entre aspas, e todas as palavras citadas precisam estar nas strings. A migração 007 reescreve `code_chunk`, e a nova versão do chunker força uma reindexação completa que reaproveita os vetores.
- Handlers de evento Delphi carregam o componente, a classe, o caption e o formulário dos controles que os disparam, então palavras da tela chegam ao `.pas`. Palavras para partes da tela (botão, grade, duplo clique) também buscam os nomes que o código dá a elas.
- Trechos focados (`retrieval.excerpts`, ligado por padrão): depois do primeiro, cada resultado mantém só as linhas numeradas em volta das palavras da pergunta, então mais resultados cabem no orçamento. O `search_code` orienta o agente a ler intervalos de linhas em vez de arquivos inteiros.
- `retrieval.doc_weight` escala os resultados de documentação na fusão, para que documentos de visão geral parem de tirar o primeiro lugar do código. O padrão é 0,5: +0,02 de MRR no benchmark público, neutro num monorepo Delphi grande.
- Grafo de código (migração 008): o indexador liga rotinas às declarações que elas citam, eventos de formulário aos seus handlers e rotinas às tabelas que o SQL delas lê ou grava. Novas ferramentas MCP `call_graph`, `trace_flow` e `find_table_usage`; o `find_references` responde pelo grafo quando pode. `retrieval.graph_weight` adiciona à fusão os vizinhos no grafo dos melhores resultados (desligado por padrão).
- O `search_code` marca o resultado com `confidence="low"` e uma nota curta quando só um método de busca achou o primeiro resultado. Nada é escondido.
- Ferramenta MCP `repo_map`: os arquivos de uma pasta que o resto do código mais cita, com suas declarações principais.
- Resumos de arquivo (`[summaries]`, desligado por padrão, migração 009): um modelo de chat escreve duas ou três frases por arquivo de código, com as palavras que o time usaria, buscadas como chunks do tipo `summary`. Ficam em cache pelo conteúdo do arquivo.
- `ragnar stats`: relatório de uso a partir das linhas de auditoria do MCP (chamadas, tokens, buscas vazias ou com baixa confiança, perguntas repetidas) e, com `--labels`, perguntas do uso real: uma busca seguida de `read_code` num arquivo que ela devolveu. As linhas de auditoria agora trazem os paths que a busca devolveu.

### Alterado

- O `ragnar init` grava por padrão as regras de busca num bloco gerenciado do `AGENTS.md` e do `CLAUDE.md`, quando o projeto tem um. `--no-agent-notes` desliga; `--agents-md` continua aceito. Rodar de novo atualiza o bloco e troca a nota antiga, que era só anexada. O `ragnar doctor` avisa quando nenhum dos dois arquivos tem as regras.
- O hook `SessionStart` do plugin também roda depois de `/clear`, de retomar a sessão e de uma compactação, e avisa o agente que o projeto está indexado e que a skill `search` escolhe a ferramenta. Um hook novo de `PreToolUse` acrescenta um lembrete de uma linha com as ferramentas do índice ao primeiro `Grep`, `Glob` ou busca no shell da sessão. Os dois só rodam em projetos com `.ragnar/config.toml` e nunca bloqueiam uma chamada.
- A skill `search` começa com três perguntas que escolhem entre as ferramentas locais e o índice, cobre `grep`, `rg` e `find` no shell e diz como conferir um resultado de baixa confiança ou possivelmente desatualizado.
- **Incompatível:** o projeto guarda os arquivos do Ragnar em duas pastas. `.ragnar/` é versionada e guarda o `config.toml` (antes o `ragnar.toml` na raiz) e as perguntas de referência em `eval/`. `.rag/` fica fora do git e guarda o `config.local.toml` (antes `ragnar.local.toml`), um arquivo `.env` que a CLI carrega e os relatórios gerados. O Ragnar não lê mais os arquivos da raiz, e o `ragnar doctor` aponta quando eles existem. Os comandos `mcp` e `embedder` nunca carregam o `.rag/.env`.
- `ragnar init` escreve no `.ragnar/config.toml` todas as chaves que um repositório pode definir, comentadas e com o valor padrão, e cria os modelos em `.rag/`. `ragnar eval run` salva o relatório em `.rag/eval/report.json` por padrão.
- **Incompatível:** o projeto mudou de `raglib` para Ragnar, incluindo CLI, variáveis de ambiente, banco, papéis e imagens. Índices montados pelo protótipo precisam ser refeitos.
- A varredura de segredos roda no texto normalizado, então cobre fontes em UTF-16.
- `index.max_file_kb` agora vale 2048 por padrão, e as execuções informam o que deixaram de fora e por quê.
- O ranking lexical pesa os termos pela raridade, remove stopwords em inglês e português e casa por stemming leve.
- Sufixos do português e acentos não separam mais uma palavra das suas variantes, nas perguntas e nos identificadores.
- A lista por arquivo ordena os arquivos por BM25 sobre a contagem de chunks, com penalidade de tamanho. `retrieval.file_weight` passa a valer 2.0, e `retrieval.rrf_k`, 30.
- A varredura de segredos lê os formulários como indexados, então dados de imagem não geram mais achados.
- Perguntas do histórico tiram prefixos de ticket e tipos de commit dos assuntos.
- `balanced` agora devolve 5 resultados em 2.000 tokens, e `economy`, 3 em 1.000: em sessões reais de agente, 4.000 tokens custaram mais contexto que o grep.
- A nota do agente e as instruções do MCP dizem quando começar pelo índice. Com o texto anterior, os agentes continuavam no grep.
- A reindexação reaproveita os vetores de chunks sem mudança, e os textos chegam ao embedder ordenados por tamanho.
- A migração 005 reescreve `code_chunk`, o que reconstrói o índice vetorial. Espere alguma demora em índices grandes e garanta memória compartilhada para builds paralelos.
- `ragnar sync --watch` relê o `repos.toml` antes de cada passada, então repositórios novos não exigem reinício. Um arquivo que deixa de ser válido mantém a lista anterior.
- Arquivos de instrução para agentes (`AGENTS.md`, `CLAUDE.md`, regras do Copilot e do Cursor) ficam fora do índice, a menos que `index.index_agent_notes` esteja ligado: o agente já os lê, e eles casavam com quase qualquer pergunta.
- O total de chunks usado na raridade dos termos fica em cache até um indexador gravar, em vez de ser contado a cada busca.
- `index.include` e `index.exclude` aceitam um diretório escrito com `/` no fim, como o `index.summary_exclude`. Arquivos já indexados nesse diretório saem do índice com `ragnar index --full`.
- No exemplo de Kubernetes, o servidor MCP e o worker leem o `server.toml`, e o `scripts/up.sh` repassa a eles as chaves de modelo do `.env`. O `k8s/base/embed.env` guarda todas as configurações de embedding, inclusive a URL.
- As perguntas do histórico pulam mensagens de commit que não dizem nada e tiram das respostas os arquivos alterados por mais de 5% dos commits. O benchmark também informa se algum arquivo certo está entre os 10 primeiros (`hit10`) e registra a confiança de cada busca.

### Corrigido

- Cada resumo de arquivo vai para o cache assim que o modelo responde, e uma chamada que falha deixa sem resumo só aquele arquivo. Antes, um único timeout parava a indexação inteira e perdia todos os resumos já escritos. Depois de três chamadas seguidas com falha, a execução para, para que um modelo fora do ar não deixe sem resumo todos os arquivos alterados.
- Um erro num repositório do `ragnar sync`, de banco ou inesperado, não derruba mais o worker: os outros repositórios e a próxima passagem continuam.
- O `.ragnar/config.toml` de um repositório não trava nem derruba mais o worker de indexação. Os tamanhos de `[index]` precisam ser positivos, `legacy_encoding` precisa ser uma codificação conhecida, e `[index]` e `[project]` precisam ser tabelas.
- Fetch e clone desistem de uma conexão parada em vez de congelar o worker.
- O `ragnar init` mantém o fim de linha do `AGENTS.md`, do `CLAUDE.md` e do `.gitignore`, e não mexe no arquivo quando os marcadores do Ragnar estão incompletos ou fora de ordem.
- O hook `PreToolUse` reconhece buscas no shell depois de um argumento entre aspas, e mostra o lembrete mesmo quando a entrada do hook não traz o id da sessão.
- Toda ferramenta MCP que devolve texto do repositório o marca como dado, e os resumos de arquivo e as notas contextuais não deixam mais o conteúdo do arquivo fechar as tags que o delimitam no prompt.
- Palavras acentuadas ficam inteiras no índice lexical.
- Comentários ficam com a declaração que vem depois deles.
- Pastas chamadas `build`, `target` ou `Win32` não escondem mais fontes reais.
- Units Delphi que a gramática não consegue ler mantêm as fronteiras de rotina e os símbolos.
- Literais gigantes se dividem em chunks que cabem no orçamento.
- Chunks com um arquivo inteiro agora citam suas declarações, e arquivos C#, o namespace.
- Um 4xx do embedder vira um erro claro, e as novas tentativas esperam entre si.
- O orçamento de tokens pula um resultado que não cabe e mantém os menores que vêm depois dele.
- O baseline de grep mantém palavras acentuadas inteiras e também busca a forma sem acento, para que perguntas em português tenham uma comparação justa.
- `ragnar migrate --resize-embeddings` também limpa o grafo de código. Antes, um token que citava um repositório pelo nome ainda lia as arestas antigas.
- `ragnar sync --repo` vale para todas as passagens do `--watch`, e um nome que não está no `repos.toml` é um erro.
- `ragnar stats` lê linhas de auditoria com prefixo, como a saída do `docker compose logs`.
- `ragnar smoke` e `ragnar load` avisam com uma mensagem de erro quando falta o `RAGNAR_MCP_TOKEN`.
- A nota contextual fica numa linha só, então o `read_code` e os trechos focados mantêm os números de linha certos.
- O Job de migração do Kubernetes lê a dimensão do embedding do `embed.env`.
- `scripts/index.sh --target k8s` roda o indexador num pod temporário, pelos Services. Antes ele passava por `kubectl port-forward`, que encerra quando o Postgres fecha uma conexão encaminhada, e a execução falhava depois das primeiras consultas. Um clone local viaja como git bundle.
- `scripts/index.sh` no Compose usa o `server.toml`, como o worker, então a próxima passagem do worker não reindexa tudo.
- Um banco inacessível vira uma mensagem de erro da CLI, não um traceback.
- `ragnar load` abre uma sessão MCP nova a cada 10 segundos. Uma conexão mantida aberta fica no pod que alcançou primeiro, então antes os pods criados pelo autoscaler não recebiam tráfego. Uma falha de conexão agora conta como erro, em vez de encerrar o teste.
- A documentação do exemplo e o `SECURITY.md` dizem que o Docker Desktop publica o `LoadBalancer` do Kubernetes em todas as interfaces de rede, e que o WSL precisa de cgroup v2 para versões recentes do Kubernetes.
