"""Builds benchmark/report.html from the run output in work/. Every number comes from those files.

Run: uv run python benchmark/report.py [--final final] [--baseline v0]
"""

from __future__ import annotations

import argparse
import html
import json
import random
import sys
from datetime import date
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).parent))
from bench import ROOT, WORK, manifest  # noqa: E402

LANGUAGE_NAMES = {"delphi": "Delphi", "java": "Java", "csharp": "C# / .NET", "go": "Go"}
CATEGORY_NAMES = {
    "concept": "Conceito", "symbol": "Símbolo", "callers": "Quem chama", "flow": "Fluxo entre arquivos",
    "config": "Configuração", "ptbr": "Pergunta em português", "form": "Formulário Delphi",
}


def load(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def ci(a: list[float], b: list[float], n: int = 4000, seed: int = 7) -> tuple[float, float, float]:
    """Mean of b - a and its paired 95% bootstrap interval."""
    rnd, d = random.Random(seed), [y - x for x, y in zip(a, b)]
    means = sorted(mean(rnd.choices(d, k=len(d))) for _ in range(n))
    return mean(d), means[int(0.025 * n)], means[int(0.975 * n)]


def answerable(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["relevant"]]


def stats(rows: list[dict], arm: str) -> dict:
    scored = [r[arm] for r in answerable(rows)]
    if not scored:
        return {"n": 0, "mrr": 0, "recall5": 0, "recall10": 0, "solved": 0, "tokens": 0, "ms": 0}
    return {"n": len(scored), "mrr": mean(a["rr"] for a in scored), "recall5": mean(a["recall5"] for a in scored),
            "recall10": mean(a["recall10"] for a in scored), "solved": mean(a["solved"] for a in scored),
            "tokens": median(a["tokens"] for a in scored), "ms": median(a["ms"] for a in scored)}


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def num(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def thousands(value: float) -> str:
    return f"{round(value):,}".replace(",", ".")


def pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def signed(value: float) -> str:
    return f"{value:+.3f}".replace(".", ",")


def bars(groups: list[tuple[str, float, float]], first: str, second: str, title: str, fmt=num) -> str:
    """Grouped horizontal bars on a 0 to 1 scale, with one label per group pair."""
    width, left, right, row, top = 720, 190, 56, 46, 8
    plot = width - left - right
    height = top + row * len(groups) + 26
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">']
    for tick in (0, 0.25, 0.5, 0.75, 1):
        x = left + plot * tick
        parts.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{height - 22}"/>'
                     f'<text class="axis" x="{x:.1f}" y="{height - 6}" text-anchor="middle">{num(tick)}</text>')
    for i, (name, a, b) in enumerate(groups):
        y = top + i * row + 7
        parts.append(f'<text class="label" x="{left - 12}" y="{y + 15}" text-anchor="end">{esc(name)}</text>')
        for j, (series, value, cls) in enumerate(((first, a, "s1"), (second, b, "s2"))):
            w = max(2, plot * value)
            by = y + j * 16
            parts.append(f'<rect class="{cls}" x="{left}" y="{by}" width="{w:.1f}" height="13" rx="3" '
                         f'data-tip="{esc(name)}: {esc(series)} {esc(fmt(value))}"/>'
                         f'<text class="value" x="{left + w + 6:.1f}" y="{by + 11}">{esc(fmt(value))}</text>')
    parts.append("</svg>")
    return "".join(parts)


def legend(first: str, second: str) -> str:
    return (f'<div class="legend"><span><i class="s1"></i>{esc(first)}</span>'
            f'<span><i class="s2"></i>{esc(second)}</span></div>')


def table(head: list[str], rows: list[list[str]], numeric_from: int = 1) -> str:
    th = "".join(f'<th class="{"n" if i >= numeric_from else ""}">{esc(h)}</th>' for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td class="{"n" if i >= numeric_from else ""}">{c}</td>' for i, c in enumerate(r)) + "</tr>"
                   for r in rows)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def comparison(rows: list[dict], key, names: dict | None, order: list[str]) -> tuple[list, list]:
    groups, lines = [], []
    for value in order:
        group = [r for r in rows if key(r) == value]
        grep, ragnar = stats(group, "grep"), stats(group, "ragnar")
        if not grep["n"]:
            continue
        name = (names or {}).get(value, value)
        groups.append((f'{name} ({grep["n"]})', grep["mrr"], ragnar["mrr"]))
        lines.append([esc(name), str(grep["n"]), num(grep["mrr"]), num(ragnar["mrr"]), pct(grep["solved"]), pct(ragnar["solved"]),
                      thousands(grep["tokens"]), thousands(ragnar["tokens"])])
    return groups, lines


COMPARE_HEAD = ["Grupo", "Perguntas", "MRR grep", "MRR Ragnar", "Resolvidas grep", "Resolvidas Ragnar",
                "Tokens grep (mediana)", "Tokens Ragnar (mediana)"]


def improvements_rows(data: list[dict]) -> str:
    out = []
    for item in data:
        out.append(f'<article class="fix"><h3>{esc(item["title"])}</h3><p><b>Problema:</b> {esc(item["problem"])}</p>'
                   f'<p><b>Correção:</b> {esc(item["fix"])}</p><p class="evidence"><b>Evidência:</b> {esc(item["evidence"])}</p></article>')
    return "".join(out)


def agent_section(rows: list[dict]) -> str:
    done = [r for r in rows if "failed" not in r]
    if not done:
        return ""
    by_arm = {arm: [r for r in done if r["arm"] == arm] for arm in ("grep", "ragnar")}
    pairs = {r["id"]: r for r in by_arm["ragnar"]}
    both = [(r, pairs[r["id"]]) for r in by_arm["grep"] if r["id"] in pairs]
    lines = []
    for label, pick in (("Só ferramentas locais", lambda p: p[0]), ("Locais + Ragnar", lambda p: p[1])):
        chosen = [pick(p) for p in both]
        lines.append([label, str(len(chosen)), pct(mean(r["correct"] for r in chosen)), num(mean(r["turns"] or 0 for r in chosen), 1),
                      thousands(median(r["input_tokens"] for r in chosen)), thousands(median(r["output_tokens"] for r in chosen)),
                      f'{median(r["seconds"] for r in chosen):.0f} s'.replace(".", ","), f'US$ {sum(r["cost_usd"] or 0 for r in chosen):.2f}'])
    failed = len(rows) - len(done)
    delta = ci([a["input_tokens"] for a, _ in both], [b["input_tokens"] for _, b in both])
    relative = median(b["input_tokens"] for _, b in both) / max(1, median(a["input_tokens"] for a, _ in both))
    wins = sum(b["input_tokens"] < a["input_tokens"] for a, b in both)
    note = (f"Mediana de contexto com Ragnar: {pct(relative)} da mediana sem ele. O Ragnar consumiu menos contexto em {wins} de "
            f"{len(both)} pares. Diferença média de {thousands(delta[0])} tokens, com intervalo de 95% entre "
            f"{thousands(delta[1])} e {thousands(delta[2])}.")
    if failed:
        note += f" {failed} execuções falharam e ficaram de fora."
    return ('<section id="agente"><h2>Agente real, antes e depois</h2>'
            f'<p>O Claude Code respondeu as mesmas perguntas do holdout nos dois modos, em {len(both)} pares. Uma resposta conta como certa '
            'quando cita um arquivo esperado. O modo com Ragnar ainda tem Grep, Read e Glob: o agente escolhe o que usar.</p>'
            + table(["Modo", "Execuções", "Respostas certas", "Turnos (média)", "Contexto lido (mediana)", "Tokens gerados (mediana)",
                     "Tempo (mediana)", "Custo total"], lines)
            + f'<p class="note">{esc(note)} O contexto soma tokens novos e lidos de cache em todos os turnos.</p></section>')


def build(args: argparse.Namespace) -> str:
    final = load(WORK / args.final / "eval.json", [])
    baseline = load(WORK / args.baseline / "eval.json", [])
    index = load(WORK / args.final / "index.json", {"repos": []})
    base_index = load(WORK / args.baseline / "index.json", {"repos": []})
    extras = load(ROOT / "report_notes.json", {})
    agents = load(WORK / args.final / "agent.json", [])
    secrets = load(WORK / "secrets.json", {})
    entries = manifest()
    languages = list(dict.fromkeys(e["language"] for e in entries))

    holdout = [r for r in final if r["split"] == "holdout"]
    dev = [r for r in final if r["split"] == "dev"]
    grep, ragnar = stats(holdout, "grep"), stats(holdout, "ragnar")
    pairs = answerable(holdout)
    gain = ci([r["grep"]["rr"] for r in pairs], [r["ragnar"]["rr"] for r in pairs]) if pairs else (0, 0, 0)
    solved_gain = ci([float(r["grep"]["solved"]) for r in pairs], [float(r["ragnar"]["solved"]) for r in pairs]) if pairs else (0, 0, 0)
    ratio = grep["tokens"] / max(1, ragnar["tokens"])

    lang_groups, lang_lines = comparison(holdout, lambda r: r["language"], LANGUAGE_NAMES, languages)
    repo_groups, repo_lines = comparison(holdout, lambda r: r["repo"], None, [e["name"] for e in entries])
    curated = [r for r in final if r["source"] == "curated"]
    cat_groups, cat_lines = comparison(curated, lambda r: r["category"], CATEGORY_NAMES, list(CATEGORY_NAMES))
    source_groups, source_lines = comparison(holdout, lambda r: r["source"], {"curated": "Curadas", "history": "Histórico de commits"},
                                             ["curated", "history"])
    split_lines = [[label, str(stats(rows, "ragnar")["n"]), num(stats(rows, "grep")["mrr"]), num(stats(rows, "ragnar")["mrr"]),
                    pct(stats(rows, "grep")["solved"]), pct(stats(rows, "ragnar")["solved"])]
                   for label, rows in (("dev (guiou os ajustes)", dev), ("holdout (nunca lido no ajuste)", holdout))]

    common = {r["repo"] for r in baseline}
    v0 = [r for r in baseline if r["split"] == "holdout"]
    now = [r for r in holdout if r["repo"] in common]
    now_ids = {r["id"]: r for r in now}
    paired = [(r, now_ids[r["id"]]) for r in answerable(v0) if r["id"] in now_ids]
    evolution_lines, evolution_groups = [], []
    for value in sorted(common):
        a, b = [r for r in v0 if r["repo"] == value], [r for r in now if r["repo"] == value]
        sa, sb = stats(a, "ragnar"), stats(b, "ragnar")
        if sa["n"]:
            evolution_groups.append((f'{value} ({sa["n"]})', sa["mrr"], sb["mrr"]))
            evolution_lines.append([esc(value), str(sa["n"]), num(sa["mrr"]), num(sb["mrr"]), pct(sa["solved"]), pct(sb["solved"]),
                                    num(sa["recall10"]), num(sb["recall10"])])
    evolution_gain = ci([a["ragnar"]["rr"] for a, _ in paired], [b["ragnar"]["rr"] for _, b in paired]) if paired else (0, 0, 0)
    v0_stats, now_stats = stats(v0, "ragnar"), stats(now, "ragnar")

    negatives = [r for r in final if r["category"] == "negative"]
    negative_note = ""
    if negatives:
        negative_note = (f"Nas {len(negatives)} perguntas sem resposta no repositório, a mediana foi de "
                         f"{thousands(median(r['grep']['tokens'] for r in negatives))} tokens com grep e "
                         f"{thousands(median(r['ragnar']['tokens'] for r in negatives))} com Ragnar. O Ragnar não diz \"não existe\": "
                         "devolve os trechos mais próximos, e cabe ao agente concluir que eles não respondem.")

    base_by_repo = {r["repo"]: r for r in base_index["repos"]}
    index_lines = [[esc(r["repo"]), str(r["files"]), thousands(r["chunks"]), thousands(r["symbols"]), f'{r["seconds"]:.0f} s',
                    f'{r["chunks"] / max(r["seconds"], 0.1):.1f}'.replace(".", ","),
                    thousands(base_by_repo[r["repo"]]["chunks"]) if r["repo"] in base_by_repo else "-",
                    thousands(base_by_repo[r["repo"]]["symbols"]) if r["repo"] in base_by_repo else "-"]
                   for r in index["repos"]]
    chunks_total = sum(r["chunks"] for r in index["repos"])
    seconds_total = sum(r["seconds"] for r in index["repos"])
    size = index.get("database_bytes", 0) / 1024 / 1024

    secret_repos = [name for name, s in secrets.items() if s["with_findings"]]
    headline = extras.get("headline") or (
        f"Com Ragnar, a resposta aparece mais alto e custa {ratio:.0f} vezes menos contexto" if gain[1] > 0 else
        f"Ragnar custa {ratio:.0f} vezes menos contexto, sem ganho comprovado de posição")
    hero = "".join(
        f'<div class="tile"><div class="big">{value}</div><div class="what">{esc(what)}</div><div class="sub">{esc(sub)}</div></div>'
        for value, what, sub in (
            (num(ragnar["mrr"]), "MRR com Ragnar", f'contra {num(grep["mrr"])} do grep'),
            (pct(ragnar["solved"]), "perguntas resolvidas", f'contra {pct(grep["solved"])} do grep'),
            (thousands(ragnar["tokens"]), "tokens por pergunta", f'contra {thousands(grep["tokens"])} do grep'),
            (str(ragnar["n"]), "perguntas no holdout", "nunca lidas durante os ajustes"),
        ))
    limits = "".join(f"<li>{esc(t)}</li>" for t in extras.get("limits", []))
    next_steps = "".join(f"<li>{esc(t)}</li>" for t in extras.get("next", []))
    experiments = extras.get("experiments", [])
    experiments_html = ""
    if experiments:
        experiments_html = ('<section id="experimentos"><h2>Experimentos que decidiram os padrões</h2>'
                            '<p>Medidos só nas perguntas dev.</p>'
                            + table(["Experimento", "Resultado", "Decisão"], [[esc(e["name"]), esc(e["result"]), esc(e["decision"])]
                                                                             for e in experiments], numeric_from=9)
                            + "</section>")
    secret_note = ""
    if secret_repos:
        secret_note = (f"<p>O gitleaks marcou arquivos em {len(secret_repos)} dos {len(secrets)} repositórios "
                       f"({esc(', '.join(secret_repos))}). Com o padrão <code>fail</code>, esses repositórios não indexariam. "
                       "O benchmark roda sem a varredura, e <code>index.secret_action = \"skip\"</code> agora cobre esse caso em produção.</p>")

    repos_list = "".join(f'<li><b>{esc(e["name"])}</b> ({esc(LANGUAGE_NAMES[e["language"]])}), commit <code>{esc(e["snapshot"])}</code></li>'
                         for e in entries)
    return TEMPLATE.format(
        title="Ragnar benchmark", date=date.today().strftime("%d/%m/%Y"), headline=esc(headline), hero=hero,
        gain=signed(gain[0]), lo=signed(gain[1]), hi=signed(gain[2]),
        solved_gain=signed(solved_gain[0]), solved_lo=signed(solved_gain[1]), solved_hi=signed(solved_gain[2]),
        recall_g=num(grep["recall10"]), recall_r=num(ragnar["recall10"]),
        legend=legend("grep + leitura (sem Ragnar)", "Ragnar"),
        lang_chart=bars(lang_groups, "grep", "Ragnar", "MRR por linguagem"), lang_table=table(COMPARE_HEAD, lang_lines),
        cat_chart=bars(cat_groups, "grep", "Ragnar", "MRR por tipo de pergunta"), cat_table=table(COMPARE_HEAD, cat_lines),
        repo_table=table(COMPARE_HEAD, repo_lines), source_table=table(COMPARE_HEAD, source_lines),
        split_table=table(["Conjunto", "Perguntas", "MRR grep", "MRR Ragnar", "Resolvidas grep", "Resolvidas Ragnar"], split_lines),
        negative_note=esc(negative_note),
        evo_legend=legend("Ragnar v0 (início)", "Ragnar final"),
        evo_chart=bars(evolution_groups, "v0", "final", "MRR da v0 e da versão final"),
        evo_table=table(["Repositório", "Perguntas", "MRR v0", "MRR final", "Resolvidas v0", "Resolvidas final", "Recall@10 v0", "Recall@10 final"],
                        evolution_lines),
        evo_mrr_a=num(v0_stats["mrr"]), evo_mrr_b=num(now_stats["mrr"]), evo_gain=signed(evolution_gain[0]),
        evo_lo=signed(evolution_gain[1]), evo_hi=signed(evolution_gain[2]), evo_n=len(paired),
        agent=agent_section(agents), experiments=experiments_html,
        improvements=improvements_rows(extras.get("improvements", [])),
        limits=limits, next_steps=next_steps,
        index_table=table(["Repositório", "Arquivos", "Chunks", "Símbolos", "Tempo", "Chunks/s", "Chunks v0", "Símbolos v0"], index_lines),
        chunks_total=thousands(chunks_total), hours=num(seconds_total / 3600, 1), size=thousands(size),
        latency=thousands(ragnar["ms"]), grep_latency=thousands(grep["ms"]), secret_note=secret_note, repos_list=repos_list,
    )


TEMPLATE = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>
<style>
:root {{
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink2: #52514e; --muted: #6b6a65;
  --grid: #e1e0d9; --base: #c3c2b7; --ring: rgba(11,11,11,.10); --s1: #2a78d6; --s2: #eb6834; --code: #f0efec;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink2: #c3c2b7; --muted: #a3a29b;
    --grid: #2c2c2a; --base: #383835; --ring: rgba(255,255,255,.10); --s1: #3987e5; --s2: #d95926; --code: #2c2c2a;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink2: #c3c2b7; --muted: #a3a29b;
  --grid: #2c2c2a; --base: #383835; --ring: rgba(255,255,255,.10); --s1: #3987e5; --s2: #d95926; --code: #2c2c2a;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--page); color: var(--ink); font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 980px; margin: 0 auto; padding: 40px 20px 72px; }}
h1 {{ font-size: clamp(1.7rem, 4vw, 2.5rem); line-height: 1.15; margin: 8px 0 12px; letter-spacing: -.01em; }}
h2 {{ font-size: 1.4rem; margin: 0 0 8px; }}
h3 {{ font-size: 1rem; margin: 0 0 6px; }}
p {{ margin: 0 0 12px; color: var(--ink2); max-width: 74ch; }}
.kicker {{ color: var(--muted); font-size: .85rem; margin: 0; }}
section {{ margin-top: 44px; }}
.card {{ background: var(--surface); border: 1px solid var(--ring); border-radius: 10px; padding: 18px; margin: 14px 0; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin: 22px 0; }}
.tile {{ background: var(--surface); border: 1px solid var(--ring); border-radius: 10px; padding: 16px; }}
.big {{ font-size: 2rem; font-weight: 650; line-height: 1.1; }}
.what {{ font-size: .92rem; margin-top: 6px; }}
.sub {{ color: var(--muted); font-size: .85rem; }}
.legend {{ display: flex; gap: 18px; flex-wrap: wrap; font-size: .85rem; color: var(--ink2); margin-bottom: 8px; }}
.legend i {{ display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }}
.legend i.s1, rect.s1 {{ background: var(--s1); fill: var(--s1); }}
.legend i.s2, rect.s2 {{ background: var(--s2); fill: var(--s2); }}
svg {{ width: 100%; height: auto; display: block; }}
svg .grid {{ stroke: var(--grid); stroke-width: 1; }}
svg text {{ font: 13px system-ui, -apple-system, "Segoe UI", sans-serif; fill: var(--ink2); }}
svg .axis {{ font-size: 12px; fill: var(--muted); font-variant-numeric: tabular-nums; }}
svg .label {{ fill: var(--ink); }}
svg .value {{ font-size: 12px; font-variant-numeric: tabular-nums; }}
.chart {{ overflow-x: auto; }}
.chart svg {{ min-width: 560px; }}
.scroll {{ overflow-x: auto; margin: 10px 0; }}
table {{ border-collapse: collapse; width: 100%; font-size: .88rem; }}
th, td {{ padding: 7px 10px; border-bottom: 1px solid var(--grid); text-align: left; vertical-align: top; }}
th {{ color: var(--muted); font-weight: 600; white-space: nowrap; }}
.n {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.note {{ color: var(--muted); font-size: .88rem; }}
.fixes {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }}
.fix {{ background: var(--surface); border: 1px solid var(--ring); border-radius: 10px; padding: 16px; }}
.fix p {{ font-size: .9rem; margin-bottom: 6px; }}
.evidence {{ color: var(--ink); }}
code {{ background: var(--code); padding: 1px 5px; border-radius: 4px; font-size: .88em; }}
details {{ margin: 10px 0; }}
summary {{ cursor: pointer; color: var(--ink2); }}
ul {{ color: var(--ink2); padding-left: 20px; max-width: 74ch; }}
li {{ margin-bottom: 6px; }}
nav {{ display: flex; gap: 14px; flex-wrap: wrap; font-size: .88rem; }}
nav a {{ color: var(--ink2); }}
#tip {{ position: fixed; pointer-events: none; background: var(--ink); color: var(--page); padding: 4px 8px; border-radius: 6px; font-size: .8rem; }}
</style>
</head>
<body>
<main>
<p class="kicker">Benchmark do Ragnar · {date}</p>
<h1>{headline}</h1>
<p>Comparação entre um agente com grep e o Ragnar em 8 projetos open source, em Delphi, Java, C# e Go. Os números principais vêm de
perguntas que ficaram fora de todos os ajustes.</p>
<div class="tiles">{hero}</div>
<p>O ganho de MRR é de <b>{gain}</b>, com intervalo de 95% entre {lo} e {hi}. O ganho em perguntas resolvidas é de {solved_gain}, entre
{solved_lo} e {solved_hi}. Recall@10 foi de {recall_g} no grep para {recall_r} no Ragnar. MRR mede quão alto vem o primeiro arquivo certo:
1 é primeiro lugar. Uma pergunta só conta como resolvida quando a evidência da resposta chega ao contexto.</p>
<nav>
<a href="#linguagem">Por linguagem</a><a href="#tipo">Por tipo de pergunta</a><a href="#agente">Agente real</a><a href="#evolucao">Antes e depois das melhorias</a>
<a href="#melhorias">Melhorias</a><a href="#limites">Limites</a><a href="#metodo">Método</a>
</nav>

<section id="linguagem">
<h2>Por linguagem</h2>
<p>MRR no holdout, com o número de perguntas entre parênteses.</p>
<div class="card">{legend}<div class="chart">{lang_chart}</div></div>
{lang_table}
<details><summary>Por repositório e por origem da pergunta</summary>{repo_table}{source_table}</details>
</section>

<section id="tipo">
<h2>Por tipo de pergunta</h2>
<p>Só perguntas curadas, somando dev e holdout: são poucas por tipo. Use esta parte para ver a direção, não o tamanho do efeito.</p>
<div class="card">{legend}<div class="chart">{cat_chart}</div></div>
{cat_table}
<p class="note">{negative_note}</p>
</section>

{agent}

<section id="evolucao">
<h2>Antes e depois das melhorias</h2>
<p>Mesmas perguntas de holdout, nos {evo_n} casos dos 5 repositórios indexados nas duas versões. O MRR foi de {evo_mrr_a} na v0 para
{evo_mrr_b} na versão final: diferença de {evo_gain}, com intervalo de 95% entre {evo_lo} e {evo_hi}.</p>
<div class="card">{evo_legend}<div class="chart">{evo_chart}</div></div>
{evo_table}
</section>

{experiments}

<section id="melhorias">
<h2>O que mudou no projeto</h2>
<div class="fixes">{improvements}</div>
</section>

<section id="limites">
<h2>Limites deste resultado</h2>
<ul>{limits}</ul>
</section>

<section>
<h2>Próximos passos</h2>
<ul>{next_steps}</ul>
</section>

<section id="metodo">
<h2>Método</h2>
<p>Cada pergunta rodou nos dois braços sobre o mesmo snapshot, fixado por commit. <b>Sem Ragnar:</b> um <code>git grep</code> por termo, arquivos
ordenados por termos distintos, depois os cinco primeiros lidos em sequência até a resposta aparecer. É o melhor caso do grep, porque um agente real
nem sempre percebe que já achou. <b>Com Ragnar:</b> uma chamada de <code>search_code</code>, ou a ferramenta específica em perguntas de símbolo e de
quem chama.</p>
<p>As perguntas curadas têm arquivo e evidência conferidos contra o snapshot. As do histórico vêm do assunto dos commits posteriores ao snapshot.
A divisão entre dev e holdout é um hash do id da pergunta. A rodada final partiu de um banco vazio, sem reaproveitar vetores.</p>
{split_table}
<p>Rodada final: {chunks_total} chunks em {hours} horas de CPU, banco com {size} MB. Latência mediana de {latency} ms por busca, contra {grep_latency} ms
do grep com leituras, em um banco único e com a máquina dividida com outras tarefas: não é número de produção.</p>
{secret_note}
<details><summary>Indexação por repositório</summary>{index_table}</details>
<details><summary>Projetos e commits</summary><ul>{repos_list}</ul></details>
</section>
</main>
<div id="tip" hidden></div>
<script>
const tip = document.getElementById('tip');
document.querySelectorAll('[data-tip]').forEach(el => {{
  el.addEventListener('mousemove', e => {{
    tip.textContent = el.dataset.tip; tip.hidden = false;
    tip.style.left = Math.min(e.clientX + 12, innerWidth - 240) + 'px'; tip.style.top = (e.clientY + 12) + 'px';
  }});
  el.addEventListener('mouseleave', () => {{ tip.hidden = true; }});
}});
</script>
</body>
</html>
"""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--final", default="final")
    p.add_argument("--baseline", default="v0")
    p.add_argument("--out", default=str(ROOT / "report.html"))
    args = p.parse_args()
    Path(args.out).write_text(build(args), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
