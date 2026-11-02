# Changelog

Todas as mudanças relevantes ficam registradas aqui. O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/), e as versões seguem o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

[English](CHANGELOG.md)

## [Unreleased]

### Adicionado

- Plugin para Claude Code na raiz do repositório, com as skills `ragnarize`, `search`, `index`, `evaluate`, `tune`, `maintain` e `infra`.
- Configuração por `ragnar.toml`, com presets, validação e sobrescrita por camadas.
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

### Alterado

- **Incompatível:** o projeto mudou de `raglib` para Ragnar, incluindo CLI, variáveis de ambiente, banco, papéis e imagens. Índices montados pelo protótipo precisam ser refeitos.
- A varredura de segredos roda no texto normalizado, então cobre fontes em UTF-16.
- `index.max_file_kb` agora vale 2048 por padrão, e as execuções informam o que deixaram de fora e por quê.
- O ranking lexical pesa os termos pela raridade, remove stopwords em inglês e português e casa por stemming leve.
- A reindexação reaproveita os vetores de chunks sem mudança, e os textos chegam ao embedder ordenados por tamanho.
- A migração 005 reescreve `code_chunk`, o que reconstrói o índice vetorial. Espere alguma demora em índices grandes e garanta memória compartilhada para builds paralelos.

### Corrigido

- Palavras acentuadas ficam inteiras no índice lexical.
- Comentários ficam com a declaração que vem depois deles.
- Pastas chamadas `build`, `target` ou `Win32` não escondem mais fontes reais.
- Units Delphi que a gramática não consegue ler mantêm as fronteiras de rotina e os símbolos.
- Literais gigantes se dividem em chunks que cabem no orçamento.
- Chunks com um arquivo inteiro agora citam suas declarações, e arquivos C#, o namespace.
- Um 4xx do embedder vira um erro claro, e as novas tentativas esperam entre si.
