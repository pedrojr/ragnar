"""Pure parts of the retrieval pipeline and the optional model clients."""

from __future__ import annotations

import httpx
import pytest

from ragnar import llm
from ragnar.chunker import Chunk
from ragnar.config import Contextual, Llm, RagnarError, Rerank, Retrieval
from ragnar.mcp_server import render_hits
from ragnar.search import (
    confidence,
    estimate_tokens,
    file_scores,
    fit_budget,
    focus,
    fuse,
    identifier_names,
    in_field,
    query_terms,
    quoted_terms,
    references,
    select_diverse,
    selective_terms,
    split_qualified,
    stem,
    term_weights,
    weigh_kinds,
)


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


def test_token_budget_skips_a_large_hit_and_keeps_smaller_ones_after_it():
    hits = [hit(1, content="x" * 400), hit(2, content="x" * 4000), hit(3, content="x" * 400)]
    assert [h["id"] for h in fit_budget(hits, 210)] == [1, 3]


def test_identifier_names_read_code_in_a_sentence():
    assert set(identifier_names("where is TOrder.Cancel used, foo_bar?")) == {"torder", "cancel", "foo_bar"}
    assert identifier_names("how does the order total get its discount") == []
    assert identifier_names("Validate tokens with AbstractRouter") == ["abstractrouter"]


def test_identifier_names_take_short_queries_as_names():
    assert identifier_names("find proxyLoopIteration") == ["proxyloopiteration"]
    assert identifier_names("retry") == ["retry"]
    assert set(identifier_names("retry policy")) == {"retry", "policy"}


def test_query_terms_drop_stopwords_but_never_everything():
    assert query_terms("how is the circuit opened") == ["circuit:*", "open:*"]
    assert query_terms("como a senha é salva") == ["senha:*", "salva:*"]
    assert query_terms("how is it") == ["how", "is", "it"]
    assert query_terms("getUserById") == ["getuserbyid:*", "user", "id"]


def test_portuguese_questions_reach_portuguese_names():
    assert query_terms("cancelamento do contrato já emitido") == ["cancel:*", "contrato:*", "emit:*"]
    assert query_terms("validação da configuração") == ["valid:*", "configur:*"]


def test_giant_files_do_not_win_by_size_alone():
    hits = {"contrato": {("r", "big.pas"): 1, ("r", "small.pas"): 1}, "prazo": {("r", "big.pas"): 1, ("r", "small.pas"): 1}}
    held = file_scores(hits, {("r", "big.pas"): 400, ("r", "small.pas"): 4}, {"contrato": 1.0, "prazo": 1.0})
    assert held[("r", "small.pas")] > held[("r", "big.pas")]
    same_size = {("r", "big.pas"): 40, ("r", "small.pas"): 40}
    focused = file_scores({"contrato": {("r", "big.pas"): 30, ("r", "small.pas"): 1}}, same_size, {"contrato": 1.0})
    assert focused[("r", "big.pas")] > focused[("r", "small.pas")]


def test_stem_keeps_short_words_exact():
    assert [stem(t) for t in ("escaped", "caching", "rates", "id", "user", "class")] == [
        "escap:*", "cach:*", "rate:*", "id", "user", "clas:*"]


def test_rare_terms_weigh_more_and_absent_terms_drop():
    weights = term_weights({"rare": 2, "common": 900, "absent": 0}, 1000)
    assert weights["rare"] > 10 * weights["common"] and "absent" not in weights


def test_common_terms_do_not_select_candidates():
    assert selective_terms({"rare": 2, "common": 900}, 1000) == ["rare"]
    assert selective_terms({"a": 800, "b": 900, "c": 950}, 1000) == ["a", "b"]
    assert selective_terms({"a": 0}, 1000) == []
    assert selective_terms({"rare:*": 2}, 1000) == ["rare:*"]


def test_hits_outside_the_budget_become_references():
    hits = [hit(1), hit(2), hit(3)]
    kept = hits[:1]
    out = references(hits, kept, 2)
    assert [h["id"] for h in out] == [1, 2] and out[1]["compact"] and not out[0].get("compact")


def test_references_render_without_their_content():
    full = {"id": 1, "repo": "r", "path": "a.py", "start_line": 1, "end_line": 2, "symbol": "a", "kind": "code",
            "content": "BODY"}
    ref = {**full, "id": 2, "path": "b.py", "content": "SECRET BODY", "compact": True}
    text = render_hits([full, ref])
    assert "BODY" in text and "SECRET" not in text and '<ref repo="r" path="b.py" lines="1-2"' in text


@pytest.mark.parametrize("name, expected", [
    ("TOrder.Cancel", ("TOrder", "Cancel")), ("a::b::c", ("b", "c")), ("pkg.Func()", ("pkg", "Func")),
    ("Cancel", ("", "Cancel")), ("List<T>.Add", ("", "List")), ("handler->run", ("handler", "run")),
])
def test_split_qualified(name, expected):
    assert split_qualified(name) == expected


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


def test_quoted_terms_read_messages_but_not_apostrophes():
    assert quoted_terms('erro "Cliente sem limite" ao gravar') == query_terms("Cliente sem limite")
    assert quoted_terms("mensagem 'Pedido já faturado' na tela") == query_terms("Pedido já faturado")
    assert query_terms("Cliente sem limite") == ["cliente:*", "limite:*"]
    assert quoted_terms("why don't orders ship") == []


def test_in_field_restricts_a_term_to_one_weight():
    assert in_field("cancel:*", "B") == "cancel:*B"
    assert in_field("erro", "B") == "erro:B"


def test_screen_words_bring_the_names_code_gives_them():
    assert query_terms("botão gravar") == ["botao:*", "grav:*", "button:*", "btn"]
    long = query_terms("qual tela liga o duplo clique da grade de pedidos ao handler de importacao")
    assert "import:*" in long and long.index("import:*") < long.index("form")  # expansions never push a word out
    assert "dbl" in query_terms("duplo clique na grade") and "grid" in query_terms("duplo clique na grade")
    assert query_terms("tabela de clientes") == ["tabela:*", "client:*"]


def test_focus_keeps_the_signature_and_the_lines_around_query_words():
    body = ["func Ship(order Order) error {"] + [f"    step{i}()" for i in range(1, 20)] + ["    return cancelOrder(order)", "}"]
    content = "// file: a.go\n// symbol: Ship\n" + "\n".join(body)
    out = focus({"id": 1, "start_line": 10, "content": content}, query_terms("cancel the order"))
    lines = out["content"].split("\n")
    assert out["excerpt"] and lines[:3] == ["// file: a.go", "// symbol: Ship", "10: func Ship(order Order) error {"]
    assert "30:     return cancelOrder(order)" in lines and "... lines 13-27" in lines
    assert focus({"id": 1, "start_line": 1, "content": content}, query_terms("step"))["content"] == content  # no saving
    short = {"id": 2, "start_line": 1, "content": "// file: b.go\n// symbol: X\nfunc X() {}"}
    assert focus(short, query_terms("x")) is short


def test_doc_weight_lets_code_pass_an_overview_document():
    hits = [{"id": 1, "kind": "doc"}, {"id": 2, "kind": "code"}]
    scores = {1: 0.05, 2: 0.04}
    assert [h["id"] for h in weigh_kinds(hits, scores, 1.0)] == [1, 2]
    assert [h["id"] for h in weigh_kinds(hits, scores, 0.5)] == [2, 1]


def test_confidence_needs_two_lists_to_agree():
    assert confidence([(1.0, [7, 2]), (1.0, [3, 7]), (2.0, [9])], 7) == "high"
    assert confidence([(1.0, [7, 2]), (1.0, [3, 4]), (0.0, [7])], 7) == "low"  # a list with no weight does not count


def test_low_confidence_is_rendered_as_a_hint():
    hits = [{"id": 1, "repo": "r", "path": "a.go", "start_line": 1, "end_line": 2, "symbol": "A", "kind": "code",
             "content": "x"}]
    assert 'confidence="low"' in render_hits(hits, "low") and "Low confidence" in render_hits(hits, "low")
    assert "confidence" not in render_hits(hits, "high")
