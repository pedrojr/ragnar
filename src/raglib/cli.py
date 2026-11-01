"""CLI única do exemplo: rag <comando>. `rag --help` lista os comandos."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

from .config import RagError, read_dsn, write_dsn


def cmd_init_env(args) -> None:
    """Cria .env (senhas e tokens aleatórios) e acl.json, se ainda não existem."""
    env_file, acl_file = Path(args.dir) / ".env", Path(args.dir) / "acl.json"
    if env_file.exists() and not args.force:
        print(f"{env_file} já existe (use --force para recriar)")
        return
    from .acl import hash_token, new_token

    token, restricted = new_token(), new_token()
    values = {
        "POSTGRES_PASSWORD": secrets.token_hex(16),
        "REPLICATION_PASSWORD": secrets.token_hex(16),
        "RAG_WRITER_PASSWORD": secrets.token_hex(16),
        "RAG_READER_PASSWORD": secrets.token_hex(16),
        "RAG_MCP_TOKEN": token,
        "RAG_MCP_TOKEN_RESTRICTED": restricted,
        "EMBED_MODEL": "jinaai/jina-embeddings-v2-base-code",
        "EMBED_DIM": "768",
    }
    env_file.write_text("# Gerado por `rag init-env`. Nunca commitar.\n"
                        + "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    acl = {"clients": [
        {"name": "dev-local", "token_sha256": hash_token(token), "repos": ["*"]},
        {"name": "restrito", "token_sha256": hash_token(restricted), "repos": ["sem-acesso"]},
    ]}
    acl_file.write_text(json.dumps(acl, indent=2) + "\n", encoding="utf-8")
    print(f"criados {env_file} e {acl_file}")


def cmd_token(args) -> None:
    from .acl import hash_token, new_token

    token = new_token()
    print(f"token (entregue ao cliente, não guarde): {token}")
    print("entrada para acl.json:")
    print(json.dumps({"name": args.name, "token_sha256": hash_token(token), "repos": args.repos.split(",")}))


def cmd_migrate(_args) -> None:
    from .migrate import migrate

    applied = migrate(write_dsn())
    print("migrações aplicadas: " + (", ".join(applied) if applied else "nenhuma pendente"))


def cmd_index(args) -> None:
    from .embedder import from_env
    from .indexer import index

    if args.no_secret_scan:
        print("AVISO: varredura de segredos desligada", file=sys.stderr)
    r = index(args.source, args.repo, write_dsn(), from_env(), ref=args.ref, full=args.full,
              scan=not args.no_secret_scan)
    print(f"{r.repo}: {r.mode} em {r.commit_sha[:12]}: {r.files} arquivos, {r.chunks} chunks gravados, "
          f"{r.deleted_chunks} removidos, {r.seconds}s")


def cmd_search(args) -> None:
    import psycopg

    from .embedder import from_env
    from .mcp_server import render_hits
    from .search import search

    with psycopg.connect(read_dsn()) as conn:
        hits, timing = search(conn, from_env(), [args.repo], args.query, args.k, args.path_prefix)
    print(render_hits(hits))
    print(f"embed {timing['embed'] * 1000:.0f} ms, sql {timing['sql'] * 1000:.0f} ms", file=sys.stderr)


def cmd_mcp(args) -> None:
    import uvicorn

    uvicorn.run("raglib.mcp_server:build_app", factory=True, host=args.host, port=args.port,
                log_level="warning", access_log=False)


def cmd_embedder(args) -> None:
    import uvicorn

    uvicorn.run("raglib.embed_server:build_app", factory=True, host=args.host, port=args.port,
                log_level="warning", access_log=False)


def cmd_embed_preload(_args) -> None:
    from .embed_server import preload

    preload()


def cmd_smoke(args) -> None:
    from .ops import smoke

    ok = smoke(args.url, os.environ["RAG_MCP_TOKEN"], os.environ.get("RAG_MCP_TOKEN_RESTRICTED"), args.repo,
               os.environ.get("RAG_DSN_WRITE"), os.environ.get("RAG_DSN_READ"), args.replica_host)
    sys.exit(0 if ok else 1)


def cmd_load(args) -> None:
    from .ops import load

    queries = Path(args.queries).read_text(encoding="utf-8").splitlines() if args.queries else None
    load(args.url, os.environ["RAG_MCP_TOKEN"], args.concurrency, args.duration, queries, args.rate)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="rag", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init-env", help="cria .env e acl.json locais")
    s.add_argument("--dir", default=".")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init_env)

    s = sub.add_parser("token", help="gera token e entrada de ACL")
    s.add_argument("--name", required=True)
    s.add_argument("--repos", required=True, help="lista separada por vírgula; * libera todos")
    s.set_defaults(fn=cmd_token)

    sub.add_parser("migrate", help="aplica migrações no primário (RAG_DSN_WRITE)").set_defaults(fn=cmd_migrate)

    s = sub.add_parser("index", help="base inicial ou incremento de um repositório (RAG_DSN_WRITE)")
    s.add_argument("source", help="caminho local ou URL git")
    s.add_argument("repo", help="nome do repositório no índice")
    s.add_argument("--ref", default="main", help="branch ou commit (padrão: main)")
    s.add_argument("--full", action="store_true", help="reindexa tudo")
    s.add_argument("--no-secret-scan", action="store_true", help="só para testes: pula o gitleaks")
    s.set_defaults(fn=cmd_index)

    s = sub.add_parser("search", help="busca direta no banco de leitura (depuração)")
    s.add_argument("query")
    s.add_argument("--repo", required=True)
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--path-prefix", default="")
    s.set_defaults(fn=cmd_search)

    for name, fn, port in (("mcp", cmd_mcp, 8080), ("embedder", cmd_embedder, 8000)):
        s = sub.add_parser(name, help=f"sobe o servidor {name} (HTTP)")
        s.add_argument("--host", default="0.0.0.0")
        s.add_argument("--port", type=int, default=port)
        s.set_defaults(fn=fn)

    sub.add_parser("embed-preload", help="baixa o modelo de embedding (build da imagem)").set_defaults(fn=cmd_embed_preload)

    s = sub.add_parser("smoke", help="verificação ponta a ponta via MCP e barreiras de escrita")
    s.add_argument("--url", default="http://127.0.0.1:8080/mcp")
    s.add_argument("--repo", default="yak")
    s.add_argument("--replica-host", help="host de uma réplica, para testar que ela recusa escrita")
    s.set_defaults(fn=cmd_smoke)

    s = sub.add_parser("load", help="carga de buscas via MCP")
    s.add_argument("--url", default="http://127.0.0.1:8080/mcp")
    s.add_argument("--concurrency", type=int, default=8)
    s.add_argument("--duration", type=float, default=120)
    s.add_argument("--rate", type=float, default=0, help="buscas/s no total (0 = o máximo possível)")
    s.add_argument("--queries", help="arquivo com uma consulta por linha")
    s.set_defaults(fn=cmd_load)

    args = p.parse_args(argv)
    try:
        args.fn(args)
    except RagError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
