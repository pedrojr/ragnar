"""Code graph extraction: references by name, form events and SQL tables."""

from __future__ import annotations

from ragnar import delphi, graph
from ragnar.chunker import Chunker, Symbol


def sym(name, start, end, qualified=None):
    return Symbol("a.pas", qualified or name, name, "defProc", "code", start, end, "")


def test_sql_tables_reads_and_writes():
    assert graph.sql_tables("select * from CLIENTES c join PEDIDOS p on p.cli = c.id") == [
        ("sql_read", "clientes"), ("sql_read", "pedidos")]
    assert graph.sql_tables("update or insert into ESTOQUE (ID) values (1)") == [("sql_write", "estoque")]
    assert graph.sql_tables("UPDATE dbo.Orders SET x = 1") == [("sql_write", "dbo.orders")]
    assert graph.sql_tables("delete from ITENS where id = :id") == [("sql_write", "itens")]
    assert graph.sql_tables("Error from server") == []  # no SQL verb: not SQL


def test_reference_edges_skip_declarations_and_repeat_names():
    text = "procedure Save;\nbegin\n  WriteOrder;\n  WriteOrder;\nend;\nprocedure WriteOrder;\nbegin\nend;\n"
    symbols = [sym("Save", 1, 5), sym("WriteOrder", 6, 8)]
    names = graph.useful_names({"save": 1, "writeorder": 1})
    edges = graph.reference_edges("a.pas", text, symbols, names)
    assert [(e.line, e.src_symbol, e.dst_name) for e in edges] == [(3, "Save", "writeorder")]
    # a form feed is not a line break for tree-sitter or editors: edges keep their line numbers
    edges = graph.reference_edges("a.pas", "\f" + text, symbols, names)
    assert [(e.line, e.src_symbol) for e in edges] == [(3, "Save")]


def test_useful_names_drop_short_and_common_names():
    assert graph.useful_names({"run": 1, "create": 9, "saveorder": 2}) == {"saveorder"}


def test_innermost_scope():
    scopes = graph.Scopes([sym("Unit", 1, 100, "Unit"), sym("Save", 10, 20, "Unit > Save")])
    assert (scopes.at(15), scopes.at(50), scopes.at(200)) == ("Unit > Save", "Unit", "")


def test_form_edges_and_chunk_sql():
    form = delphi.parse_form("object F: TF\n  object b: TButton\n    OnClick = bClick\n  end\nend\n")
    assert [(e.src_symbol, e.dst_name, e.kind) for e in graph.form_edges("f.dfm", form)] == [("F > b", "bclick", "event")]
    unit = (b"unit U;\ninterface\nimplementation\nprocedure P;\nbegin\n  Q.SQL.Add('select * ');\n"
            b"  Q.SQL.Add('from VENDAS');\nend;\nend.\n")
    chunk = Chunker().chunk_file("u.pas", unit)[0]
    edges = graph.sql_edges(chunk, graph.Scopes([]))
    assert [(e.dst_name, e.kind, e.line) for e in edges] == [("vendas", "sql_read", 7)]
