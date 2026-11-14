# Como contribuir

Obrigado pela ajuda. Este guia cobre o necessário para uma mudança ser aceita.

[English](CONTRIBUTING.md)

## Antes de começar

Abra uma issue para tudo que for além de uma pequena correção, para combinarmos a abordagem antes. Boas primeiras contribuições: um novo perfil de linguagem, evidências de recuperação na sua base de código e correções na documentação.

Para problemas de segurança, siga o [SECURITY.pt-BR.md](SECURITY.pt-BR.md) e não abra issue pública.

## Preparação

```bash
git clone https://github.com/pedrojr/ragnar
cd ragnar
uv sync --extra embed
uv run pytest
```

Os testes de integração precisam de Postgres com pgvector. Suba o exemplo e aponte os testes para ele:

```bash
[ -f example/.env ] || uv run ragnar init-env --dir example   # senhas para um clone novo
cd example && docker compose up -d db-primary && cd ..
set -a; . example/.env; set +a
RAGNAR_TEST_ADMIN_DSN="postgresql://postgres:$POSTGRES_PASSWORD@127.0.0.1:15432/postgres" uv run pytest
```

Eles criam e apagam um banco `ragnar_test` e nunca tocam no `ragnar`.

Para testar o plugin a partir do seu clone:

```bash
claude --plugin-dir .
claude plugin validate .
```

## Regras para uma mudança

- **Idioma.** Código, comentários, mensagens de commit e mensagens de erro ficam em inglês.
- **Documentação vai em pares.** Uma página em `docs/` precisa da sua par em `docs/pt-BR/`, e um documento da raiz ou do exemplo precisa do seu `.pt-BR.md`. Atualize os dois no mesmo pull request. Um teste garante que cada par exista.
- **Testes.** Comportamento novo precisa de teste. `uv run pytest` tem de passar.
- **Estilo.** `uv run ruff check .` tem de passar. Siga o código ao redor da sua mudança.
- **Mudanças na recuperação precisam de evidência.** Inclua um relatório do `ragnar eval`. O Recall@10 não pode cair mais de 0,02.
- **Segredos.** Nunca adicione tokens ou senhas reais, nem em testes.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/pt-br/):

```text
feat(languages): add Elixir profile
fix(indexer): handle file names with spaces
docs: explain binary Delphi forms
```

| Tipo | Uso |
| --- | --- |
| `feat` | Comportamento novo |
| `fix` | Correção de bug |
| `docs` | Só documentação |
| `refactor` | Mudança de código com o mesmo comportamento |
| `test` | Só testes |
| `chore`, `ci` | Ferramentas |

Escopos comuns: `config`, `languages`, `retrieval`, `indexer`, `sync`, `eval`, `mcp`, `plugin`, `example`. Marque mudanças incompatíveis com `!` e um rodapé `BREAKING CHANGE:`.

## Pull requests

1. Crie o branch a partir da `main`.
2. Mantenha cada pull request com um só objetivo.
3. Preencha o modelo.
4. Adicione sua mudança em `[Unreleased]` no `CHANGELOG.md` e no `CHANGELOG.pt-BR.md`.

## Versões

O Ragnar segue o [Versionamento Semântico](https://semver.org/lang/pt-BR/). Para lançar:

1. Ajuste a versão em `pyproject.toml`, `src/ragnar/__init__.py` e `.claude-plugin/plugin.json`. Um teste mantém as três iguais.
2. Mova `[Unreleased]` para a nova versão nos dois changelogs.
3. Crie a tag `vX.Y.Z` e publique um release no GitHub.
