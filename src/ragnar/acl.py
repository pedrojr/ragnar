"""Per-client tokens and the repositories each one may read.

JSON file: {"clients": [{"name": "...", "token_sha256": "...", "repos": ["myapp"]}]}.
"*" in repos allows every repository, and a glob such as "billing-*" allows the repositories it matches
(fnmatch, case-sensitive). The server stores only the token hash.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from .config import RagnarError


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

    @property
    def has_patterns(self) -> bool:
        return any(any(ch in r for ch in "*?[") for r in self.repos)

    def allows(self, repo: str) -> bool:
        return any(fnmatchcase(repo, r) for r in self.repos)

    def resolve(self, requested: str | None, known: Callable[[], list[str]] | None = None) -> list[str] | None:
        """Repositories the query may see. None = all. A client filter narrows, it never widens.

        known lists the indexed repositories; a client with globs needs it to expand them."""
        if requested:
            if not self.allows(requested):
                raise RagnarError(f"no permission for repository {requested!r}")
            return [requested]
        if self.all_repos:
            return None
        if not self.has_patterns:
            return list(self.repos)
        if known is None:
            raise RagnarError("the repository list is needed to expand this client's globs")
        return [r for r in known() if self.allows(r)]


class Acl:
    def __init__(self, clients: dict[str, Client]):
        self._by_hash = clients

    @classmethod
    def from_json(cls, text: str) -> Acl:
        data = json.loads(text)
        clients = {}
        for c in data["clients"]:
            repos = c["repos"]
            # a string would become its characters, and one "*" among them would grant every repository
            if not isinstance(repos, list) or not all(isinstance(r, str) for r in repos):
                raise ValueError(f"repos of client {c.get('name')!r} must be a list of names")
            clients[c["token_sha256"]] = Client(c["name"], tuple(repos))
        return cls(clients)

    @classmethod
    def from_file(cls, path: str | Path) -> Acl:
        try:
            return cls.from_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError) as exc:
            raise RagnarError(f"invalid ACL at {path}: {exc}") from None

    def client_for(self, authorization: str | None) -> Client | None:
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        return self._by_hash.get(hash_token(authorization[7:].strip()))
