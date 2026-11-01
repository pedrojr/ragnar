"""Token por cliente e repositórios permitidos (regra 1 da seção 9 do guia).

Arquivo JSON: {"clients": [{"name": "...", "token_sha256": "...", "repos": ["yak"]}]}.
"*" em repos libera todos. O servidor guarda só o hash do token.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from .config import RagError


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Client:
    name: str
    repos: tuple[str, ...]

    @property
    def all_repos(self) -> bool:
        return "*" in self.repos

    def resolve(self, requested: str | None) -> list[str] | None:
        """Repos que a consulta pode ver. None = todos. Filtro do cliente só restringe, nunca amplia."""
        if requested:
            if not self.all_repos and requested not in self.repos:
                raise RagError(f"sem permissão para o repositório {requested!r}")
            return [requested]
        return None if self.all_repos else list(self.repos)


class Acl:
    def __init__(self, clients: dict[str, Client]):
        self._by_hash = clients

    @classmethod
    def from_json(cls, text: str) -> Acl:
        data = json.loads(text)
        return cls({c["token_sha256"]: Client(c["name"], tuple(c["repos"])) for c in data["clients"]})

    @classmethod
    def from_file(cls, path: str | Path) -> Acl:
        try:
            return cls.from_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError) as exc:
            raise RagError(f"ACL inválida em {path}: {exc}") from None

    def client_for(self, authorization: str | None) -> Client | None:
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        return self._by_hash.get(hash_token(authorization[7:].strip()))
