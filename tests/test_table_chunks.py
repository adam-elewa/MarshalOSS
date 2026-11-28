from types import SimpleNamespace as NS

from marshal_oss.ingest import _chunk_tables, _table_cell_count


def _doc(rows):
    grid = [[NS(text=v) for v in r] for r in rows]
    tb = NS(data=NS(grid=grid, num_rows=len(rows), num_cols=len(rows[0])), prov=[])
    return NS(tables=[tb], texts=[])


def test_row_groups_repeat_header_and_cover_all_rows():
    rows = [["Site", "Lat", "Lon"]] + [[str(i), "40.1", "-74.2"] for i in range(2000)]
    doc = _doc(rows)
    assert _table_cell_count(doc) == 2001 * 3
    chunks = _chunk_tables(doc, max_chars=500)
    assert len(chunks) > 1
    assert all(c["text"].startswith("Site | Lat | Lon\n") for c in chunks)
    assert all(len(c["text"]) <= 500 for c in chunks)
    body = [ln for c in chunks for ln in c["text"].split("\n")[1:]]
    assert len(body) == 2000
    assert chunks[0]["heading"].startswith("Table rows 2–")
    assert chunks[-1]["heading"].endswith("–2001")


def test_header_only_table():
    chunks = _chunk_tables(_doc([["A", "B"]]))
    assert len(chunks) == 1 and chunks[0]["text"].startswith("A | B")
