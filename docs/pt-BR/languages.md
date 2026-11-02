# Linguagens

Cada linguagem tem um perfil em [`src/ragnar/languages.py`](../../src/ragnar/languages.py). O perfil decide como dividir os arquivos, quais declarações viram símbolos, o que é teste e o que ignorar.

[English](../languages.md)

## Suportadas

| Linguagem | Extensões | Chunking | Símbolos |
| --- | --- | --- | --- |
| Python | `.py`, `.pyi` | AST | Classes, funções |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` | AST | Classes, funções, métodos, arrow functions |
| TypeScript | `.ts`, `.tsx`, `.mts`, `.cts` | AST | Os mesmos, mais interfaces, tipos, enums, namespaces |
| Java | `.java` | AST | Classes, interfaces, enums, records, métodos |
| Kotlin | `.kt`, `.kts` | AST | Classes, objetos, funções |
| C# | `.cs` | AST | Namespaces, classes, interfaces, structs, records, métodos |
| Go | `.go` | AST | Tipos, funções, métodos com o receptor |
| Rust | `.rs` | AST | Funções, structs, enums, traits, módulos, métodos de `impl` |
| C | `.c`, `.h` | AST | Funções, structs, enums, unions |
| C++ | `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx` | AST | Os de C, mais classes e namespaces |
| PHP | `.php` | AST | Classes, interfaces, traits, enums, métodos, funções |
| Ruby | `.rb`, `.rake` | AST | Módulos, classes, métodos |
| Swift | `.swift` | AST | Classes, structs, protocolos, enums, funções |
| Scala | `.scala`, `.sc` | AST | Classes, objetos, traits, métodos |
| Bash | `.sh`, `.bash` | AST | Funções |
| Delphi | `.pas`, `.dpr`, `.dpk`, `.lpr`, `.pp`, `.inc`, `.dfm`, `.fmx`, `.lfm` | AST e formulários | Tipos, métodos, procedures, componentes de formulário |
| SQL | `.sql` | Janelas de linhas | Nenhum |
| Markdown | `.md`, `.mdx` | Seções | Nenhum |

Qualquer outro arquivo de texto pode ser indexado por janelas de linhas, com `index.include`:

```toml
[index]
include = ["*.vue", "*.proto"]
```

## O que é ignorado

Pastas de terceiros, como `node_modules/` e `vendor/`, e saídas de build, como `dist/`, `target/`, `bin/` e `obj/`. Um nome de pasta de build abaixo de `src/`, `source/` ou `sources/` é um pacote real, então fica, e Go não tem saída de build na árvore. Cada linguagem soma as suas: `.pb.go`, `.d.ts`, `.g.cs`, `_pb2.py`, e em Delphi `__history/` e `_TLB.pas`. Também ficam de fora arquivos com marca de "gerado" nas primeiras linhas, binários, e arquivos acima de `index.max_file_kb`.

## Delphi e código legado

Projetos legados precisam de mais que uma gramática, então Delphi tem tratamento próprio.

**Encodings.** Fontes antigos costumam estar em CP-1252 ou UTF-16. O Ragnar converte para UTF-8 antes de analisar, então comentários e textos acentuados continuam pesquisáveis. `index.legacy_encoding` define a alternativa para arquivos sem marca. Todas as linguagens se beneficiam. A varredura de segredos também roda no texto convertido, porque o gitleaks não lê UTF-16.

**Units e métodos.** Os símbolos seguem o caminho `unit > tipo > método`. `TOrder.Cancel` na seção `implementation` vira `Orders > TOrder > Cancel`. As buscas ignoram maiúsculas, como o Pascal.

**Formulários.** Um `.dfm` é quase todo geometria, mas também esconde regra de negócio: comandos SQL e os eventos ligados a cada componente. O Ragnar guarda isso e descarta o resto:

```text
Form1: TForm1 Caption=Customers OnCreate=FormCreate
  btnSave: TButton Caption=Save OnClick=btnSaveClick
  qryCustomers: TADOQuery SQL=select * from customers where id = :id
```

Assim o agente vai de uma pergunta de negócio ao formulário, e do formulário ao evento no `.pas`. Descartar a geometria também reduz o custo de embedding. Strings de conexão também saem, então senhas dentro delas ficam fora do índice.

**Formulários binários.** Projetos antigos podem guardar formulários em binário (`TPF0`). O Ragnar não os lê e os ignora. Converta para texto na IDE, ou com o `convert.exe` da pasta `bin` do Delphi.

**Testes.** Os nomes de DUnit e DUnitX são reconhecidos: `TestOrders.pas`, `OrderTests.pas` e tudo em pastas `test/`.

**Código gerado.** Type libraries `_TLB.pas`, as pastas da IDE `__history/` e `__recovery/` e saídas do compilador como `Win32/Debug/` são ignoradas. Uma pasta `Win32/` simples fica: bibliotecas guardam fontes nela.

Compilação condicional e convenções de chamada quebram a gramática Pascal em units reais. Quando isso acontece, o Ragnar varre o arquivo atrás de rotinas e tipos e os usa como fronteiras de chunk e símbolos. Units grandes também são indexadas: units centrais costumam passar de 500 KB.

## Adicionar uma linguagem

1. Ache uma gramática tree-sitter com wheels no PyPI e adicione ao `pyproject.toml`.
2. Adicione um perfil a `LANGUAGES` em `src/ragnar/languages.py`. Liste os nós da AST que definem símbolos. Um jeito rápido de achá-los: analise uma amostra e imprima os tipos dos nós.
3. Adicione um caso a `CASES` em `tests/test_languages.py`. A suíte falha até toda gramática ter um.
4. Atualize esta página e sua tradução.
