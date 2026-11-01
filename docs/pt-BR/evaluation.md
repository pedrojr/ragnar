# Avaliação

O Ragnar se mede contra a alternativa: um agente com grep. Faça isso antes de confiar no índice e antes de mudar o modelo, o chunking ou as configurações de recuperação.

[English](../evaluation.md)

As skills `evaluate` e `tune` fazem isso por você. Esta página explica o que elas fazem.

## Método

Cada commit é uma pergunta real com resposta conhecida. A mensagem é a consulta, e os arquivos alterados são o resultado esperado.

1. Escolha uma data de corte. Indexe o repositório como ele era naquele momento, com outro nome, como `myapp-snap`.
2. Monte as perguntas com os commits depois dessa data. O snapshot é anterior a eles, então as respostas não vazam para o índice.
3. Passe as mesmas perguntas pelo grep e por cada configuração.
4. Compare em pares, pergunta a pergunta.

```bash
SNAP=$(git rev-list -1 --before="2026-09-01" main)
./scripts/index.sh /caminho/do/myapp myapp-snap --ref "$SNAP"     # a partir de example/
ragnar eval golden /caminho/do/myapp "$SNAP" --branch main
ragnar eval run /caminho/do/myapp "$SNAP" myapp-snap
```

`ragnar eval run` precisa de `RAGNAR_DSN_READ` e das configurações de embedding.

## Como ler o relatório

```text
42 questions
system           Recall@10    MRR   tokens
grep                 0.512  0.301    18400
configured           0.690  0.455     2900
configured vs_grep: recall gain +0.178, 95% CI [+0.081, +0.270]
```

| Coluna | Significado |
| --- | --- |
| Recall@10 | Parcela dos arquivos esperados entre os 10 primeiros |
| MRR | Quão alto aparece o primeiro arquivo esperado. 1 é o primeiro lugar |
| tokens | Contexto por pergunta. Veja a nota abaixo |
| 95% CI | Faixa do ganho, por bootstrap pareado |

Esses números ilustram o formato. Não são um benchmark.

**Um ganho só conta se o intervalo inteiro estiver acima de zero.** Um intervalo que contém zero não é evidência de ganho.

**Defina a regra de decisão antes de rodar.** Do contrário, é fácil justificar qualquer resultado.

**Abaixo de 30 perguntas, o relatório avisa que o resultado é indicativo.** Adicione perguntas escritas pelo time, um objeto JSON por linha:

```json
{"id": "q1", "query": "onde repetimos pagamentos que falharam?", "relevant": ["src/payments/retry.py"]}
```

**A coluna de tokens compara ordens de grandeza.** No grep, estima a leitura completa dos cinco primeiros arquivos, que é o que o grep deixa para o agente fazer. No Ragnar, é o contexto realmente devolvido. Os dois usam quatro caracteres por token.

## Comparar configurações

`--variant` avalia outras configurações nas mesmas perguntas:

```bash
ragnar eval run /caminho/do/myapp "$SNAP" myapp-snap \
  --variant economy:project.preset=economy \
  --variant wide:retrieval.candidates=100,retrieval.neighbor_chunks=1
```

Cada variante mostra o ganho sobre o grep e sobre a configuração atual. Opções em `[index]` mudam os chunks, então exigem um snapshot montado com esses valores.

## Limites

- Mensagens de commit são perguntas imperfeitas. Elas soam diferente do que um desenvolvedor perguntaria.
- Recall mede a recuperação, não se a tarefa foi concluída. O teste final é o time terminar o trabalho mais rápido e gastar menos.
- O histórico reflete o passado. Avalie de novo depois de grandes mudanças.
