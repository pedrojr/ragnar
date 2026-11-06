# Benchmark

Mede o Ragnar contra o que um agente faz sem ele, em projetos open source fixados em Delphi, Java, C# e Go.

[English](README.md)

Clones e saídas de execução ficam fora do git, em `repos/` e `work/`. As perguntas, os commits fixados e o relatório são versionados. O relatório tem duas versões: [inglês](report.html) e [português](report.pt-BR.html).

## O que mede

Cada pergunta roda duas vezes no mesmo snapshot.

| Braço | O que faz |
| --- | --- |
| Antes do Ragnar | Um `git grep` por termo, arquivos ordenados por termos distintos, depois os cinco primeiros lidos em sequência até a resposta aparecer. É o melhor caso do grep: um agente real nem sempre percebe que já tem a resposta |
| Depois do Ragnar | Uma chamada de `search_code`, ou `find_symbol` em perguntas de símbolo |

O `agent.py` acrescenta uma terceira medida: o Claude Code responde uma amostra das perguntas, só com ferramentas locais e também com as ferramentas do Ragnar. Os braços roteirizados mostram a qualidade da recuperação. As execuções com agente mostram se essa qualidade chega a uma sessão real.

## Perguntas

| Origem | Onde | Como a resposta é conhecida |
| --- | --- | --- |
| Curadas | `questions/curated/` | Escritas lendo o código. Cada uma tem um arquivo esperado e uma evidência, expressão regular conferida contra o snapshot |
| Histórico | `questions/history/` | O assunto de um commit posterior ao snapshot e os arquivos que ele alterou |

As curadas cobrem conceitos, símbolos, quem chama, fluxos entre arquivos, configuração, perguntas em português sobre código em inglês, formulários Delphi e perguntas sem resposta no repositório.

**As perguntas se dividem em `dev` e `holdout` por um hash do id.** As falhas de `dev` guiaram as correções. O `holdout` fica sem leitura até o relatório final, então o ajuste não consegue inflá-lo.

## Métricas

| Métrica | Significado |
| --- | --- |
| MRR | Quão alto aparece o primeiro arquivo esperado. 1 é primeiro lugar |
| Recall@5, Recall@10 | Fração dos arquivos esperados entre os 5 ou 10 primeiros |
| Resolvida | A resposta chegou ao agente: o arquivo esperado apareceu, e sua evidência estava no que o braço devolveu |
| Tokens | O que o braço colocou no contexto, a quatro caracteres por token |

## Como rodar

Você precisa de Docker, `uv`, cerca de 3 GB de disco e algumas horas em CPU: o embedding é a etapa lenta. Cada comando pode rodar de novo com segurança.

```bash
uv sync --extra embed
docker compose -f benchmark/compose.yaml up -d --wait
RAGNAR_EMBED_THREADS=10 uv run ragnar embedder --host 127.0.0.1 --port 8001 &

uv run python benchmark/bench.py prepare                  # clones, snapshots, perguntas do histórico
uv run python benchmark/validate_questions.py             # respostas curadas contra os snapshots
uv run python benchmark/bench.py index --label final      # banco vazio, depois todos os repositórios
uv run python benchmark/bench.py evaluate --label final
uv run python benchmark/report.py
```

O `PYTHONPATH` escolhe o Ragnar em teste, então uma árvore antiga passa pelos mesmos scripts:

```bash
git worktree add benchmark/work/v0-src <commit>
PYTHONPATH=benchmark/work/v0-src/src uv run python benchmark/bench.py index --label v0
```

O `RAGNAR_BENCH_SUITE` aponta os mesmos scripts para outra pasta, com seus próprios `repos.toml`, `questions/`, `repos/` e `work/`. Use para um repositório privado: o código, as perguntas e os resultados ficam fora deste repositório. O `RAGNAR_BENCH_DSN` aponta para outro banco, como o primário da infraestrutura de exemplo.

```bash
RAGNAR_BENCH_SUITE=/caminho/da/suite-privada uv run python benchmark/bench.py prepare
```

Para as execuções com agente, suba um servidor MCP no mesmo banco e defina um token. As execuções consomem uso de modelo, por isso o número de perguntas é explícito e cada execução tem um teto de gasto:

```bash
uv run python benchmark/agent.py --label final --questions 3 --model haiku
```

## Limites

- Assuntos de commit são perguntas imperfeitas e favorecem arquivos que mudam com frequência.
- Uma pergunta respondida por um arquivo grande favorece o grep: esse arquivo tem todas as palavras.
- O braço de grep roteirizado lê perguntas em português por meio de um `grep_query` em inglês, escrito à mão. As execuções com agente mostram quanto custa uma tradução real.
- A latência aqui vem de um banco sem réplicas, com a CPU também gerando embeddings. Não é um número de produção.
