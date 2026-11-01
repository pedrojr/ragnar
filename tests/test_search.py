"""Pure parts of the retrieval pipeline and the optional model clients."""

from __future__ import annotations

import httpx
import pytest

from ragnar import llm
from ragnar.chunker import Chunk
from ragnar.config import Contextual, Llm, RagnarError, Rerank, Retrieval
from ragnar.search import estimate_tokens, fit_budget, fuse, identifier_names, select_diverse


def hit(i, path="a.py", content=None):
    return {"id": i, "repo": "r", "path": path, "content": content or f"chunk {i}"}


def test_fuse_rewards_agreement_and_honors_weights():
    assert fuse([(1.0, [1, 2]), (1.0, [2, 3])], 60)[0] == 2
    assert fuse([(1.0, [1]), (3.0, [2])], 60) == [2, 1]
    assert fuse([(0.0, [1]), (1.0, [2])], 60) == [2, 1]
    assert fuse([], 60) == []


def test_per_file_cap():
    hits = [hit(1), hit(2), hit(3), hit(4, "b.py")]
    chosen = select_diverse(hits, 3, Retrieval(max_chunks_per_file=2))
    assert [h["id"] for h in chosen] == [1, 2, 4]


def test_cap_off_keeps_order():
    hits = [hit(i) for i in range(5)]
    assert [h["id"] for h in select_diverse(hits, 3, Retrieval(max_chunks_per_file=0))] == [0, 1, 2]


def test_diversity_prefers_new_content():
    same = "order total price discount"
    hits = [hit(1, "a.py", same), hit(2, "b.py", same), hit(3, "c.py", "shipping address label carrier")]
    assert [h["id"] for h in select_diverse(hits, 2, Retrieval(diversity=0.5))] == [1, 3]
    assert [h["id"] for h in select_diverse(hits, 2, Retrieval(diversity=0.0))] == [1, 2]


def test_token_budget_keeps_best_hits_and_never_empties():
    hits = [hit(1, content="x" * 400), hit(2, content="x" * 400), hit(3, content="x" * 400)]
    assert [h["id"] for h in fit_budget(hits, 210)] == [1, 2]
    assert [h["id"] for h in fit_budget(hits, 10)] == [1]
    assert fit_budget(hits, 0) == hits
    assert estimate_tokens("x" * 400) == 101


def test_identifier_names():
    assert identifier_names("where is TOrder.Cancel used, foo_bar?") == ["where", "torder", "cancel", "used", "foo_bar"]


def mock_client(handler):
    return httpx.Client(base_url="http://model.test/v1", transport=httpx.MockTransport(handler))


def test_chat_is_openai_compatible():
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, body=request.read())
        return httpx.Response(200, json={"choices": [{"message": {"content": " hi "}}]})

    chat = llm.Chat(Llm(url="http://model.test/v1", model="any"))
    chat._client = mock_client(handler)
    assert chat.complete("q") == "hi"
    assert seen["path"] == "/v1/chat/completions" and b'"model":"any"' in seen["body"].replace(b" ", b"")


def test_chat_failure_is_a_clear_error():
    chat = llm.Chat(Llm(url="http://model.test/v1", model="any"))
    chat._client = mock_client(lambda _: httpx.Response(500))
    with pytest.raises(RagnarError, match="chat model unavailable"):
        chat.complete("q")


def test_reranker_orders_by_score():
    reranker = llm.Reranker(Rerank(provider="http", url="http://model.test/rerank", model="m"))
    reranker._client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"results": [
        {"index": 0, "relevance_score": 0.1}, {"index": 1, "relevance_score": 0.9}]})))
    assert reranker.rerank("q", ["a", "b"]) == [1, 0]


class StubChat:
    def __init__(self, answer):
        self.answer = answer

    def complete(self, prompt):
        return self.answer


def test_expand_query_multi_query_and_hyde():
    assert llm.expand_query(StubChat("one\n\ntwo\nthree\nfour"), "multi_query", 3, "q") == ["one", "two", "three"]
    assert llm.expand_query(StubChat("def x(): pass"), "hyde", 3, "q") == ["def x(): pass"]


def test_contextualize_adds_the_note_to_content_and_search():
    chunk = Chunk("a.py", "s", "python", "code", 1, 2, "// file: a.py\ncode", "search")
    out = llm.contextualize(StubChat("Handles billing"), Contextual(), [chunk], {"a.py": b"code"})
    assert out[0].content.startswith("Handles billing\n") and "billing" in out[0].search_text
    assert out[0].start_line == 1
