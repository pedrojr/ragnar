# Languages

Each language has a profile in [`src/ragnar/languages.py`](../src/ragnar/languages.py). The profile decides how to split files, which declarations count as symbols, what counts as a test, and what to skip.

[Português (Brasil)](pt-BR/languages.md)

## Supported

| Language | Extensions | Chunking | Symbols |
| --- | --- | --- | --- |
| Python | `.py`, `.pyi` | AST | Classes, functions |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` | AST | Classes, functions, methods, arrow functions |
| TypeScript | `.ts`, `.tsx`, `.mts`, `.cts` | AST | Same, plus interfaces, types, enums, namespaces |
| Java | `.java` | AST | Classes, interfaces, enums, records, methods |
| Kotlin | `.kt`, `.kts` | AST | Classes, objects, functions |
| C# | `.cs` | AST | Namespaces, classes, interfaces, structs, records, methods |
| Go | `.go` | AST | Types, functions, methods with their receiver |
| Rust | `.rs` | AST | Functions, structs, enums, traits, modules, `impl` methods |
| C | `.c`, `.h` | AST | Functions, structs, enums, unions |
| C++ | `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx` | AST | Same as C, plus classes and namespaces |
| PHP | `.php` | AST | Classes, interfaces, traits, enums, methods, functions |
| Ruby | `.rb`, `.rake` | AST | Modules, classes, methods |
| Swift | `.swift` | AST | Classes, structs, protocols, enums, functions |
| Scala | `.scala`, `.sc` | AST | Classes, objects, traits, methods |
| Bash | `.sh`, `.bash` | AST | Functions |
| Delphi | `.pas`, `.dpr`, `.dpk`, `.lpr`, `.pp`, `.inc`, `.dfm`, `.fmx`, `.lfm` | AST and forms | Types, methods, procedures, form components |
| SQL | `.sql` | Line windows | None |
| Markdown | `.md`, `.mdx` | Sections | None |

Any other text file can be indexed by line windows, through `index.include`:

```toml
[index]
include = ["*.vue", "*.proto"]
```

## What is skipped

Vendored folders such as `node_modules/` and `vendor/`, and build output such as `dist/`, `target/`, `bin/` and `obj/`. A build folder name below `src/`, `source/` or `sources/` is a real package, so it stays, and Go has no in-tree build output. Each language adds its own: `.pb.go`, `.d.ts`, `.g.cs`, `_pb2.py`, Delphi's `__history/` and `_TLB.pas`. Files with a "generated" marker in their first lines, binary files, and files above `index.max_file_kb` are skipped too.

## Delphi and legacy code

Legacy projects need more than a grammar, so Delphi has specific handling.

**Encodings.** Old sources are often CP-1252 or UTF-16. Ragnar converts them to UTF-8 before parsing, so accented comments and strings stay searchable. `index.legacy_encoding` sets the fallback for files with no marker. All languages benefit from this. The secret scan also runs on the converted text, because gitleaks cannot read UTF-16.

**Units and methods.** Symbols follow the `unit > type > method` path. `TOrder.Cancel` in the `implementation` section resolves to `Orders > TOrder > Cancel`. Lookups ignore case, as Pascal does.

**Forms.** A `.dfm` file is mostly geometry, but it also hides business logic: SQL statements and the handlers bound to each component. Ragnar keeps these and drops the rest:

```text
Form1: TForm1 Caption=Customers OnCreate=FormCreate
  btnSave: TButton Caption=Save OnClick=btnSaveClick
  qryCustomers: TADOQuery SQL=select * from customers where id = :id
```

An agent can then go from a business question to the form, and from the form to the handler in the `.pas` file. Dropping geometry also lowers the embedding cost. Connection strings are dropped too, so passwords inside them stay out of the index.

**Binary forms.** Old projects may store forms as binary (`TPF0`). Ragnar cannot read those, and it skips them. Convert them to text in the IDE, or with `convert.exe` from the Delphi `bin` folder.

**Tests.** DUnit and DUnitX naming is recognized: `TestOrders.pas`, `OrderTests.pas`, and anything in a `test/` folder.

**Generated code.** `_TLB.pas` type libraries, the IDE's `__history/` and `__recovery/` folders, and compiler output such as `Win32/Debug/` are skipped. A plain `Win32/` folder stays: libraries keep sources there.

Conditional compilation and calling conventions break the Pascal grammar on real units. When that happens, Ragnar scans the file for routines and types, and uses them for chunk boundaries and symbols. Large units are indexed too: core units often pass 500 KB.

## Adding a language

1. Find a tree-sitter grammar with wheels on PyPI, and add it to `pyproject.toml`.
2. Add a profile to `LANGUAGES` in `src/ragnar/languages.py`. List the AST nodes that define symbols. A quick way to find them: parse a sample and print the node types.
3. Add a case to `CASES` in `tests/test_languages.py`. The suite fails until every grammar has one.
4. Update this page and its translation.
