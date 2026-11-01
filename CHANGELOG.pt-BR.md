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

### Alterado

- **Incompatível:** o projeto mudou de `raglib` para Ragnar, incluindo CLI, variáveis de ambiente, banco, papéis e imagens. Índices montados pelo protótipo precisam ser refeitos.
- A varredura de segredos roda no texto normalizado, então cobre fontes em UTF-16.

### Corrigido

- Palavras acentuadas ficam inteiras no índice lexical.
- Comentários ficam com a declaração que vem depois deles.
