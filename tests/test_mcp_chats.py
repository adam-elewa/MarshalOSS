import asyncio

import pytest

from marshal_oss import mcp_chats
from marshal_oss.mcp_chats import list_chats, read_messages, search_messages


def _payload():
    msgs = [
        # 2020-07-31 23:30 New York = 2020-08-01 03:30 UTC
        {"id": "m0", "body": "meet at the park", "timestamp": "2020-08-01T03:30:00+00:00", "from": "Me", "status": "Sent"},
        {"id": "m1", "body": "Who is this? call 555-010-0042", "timestamp": "2020-08-01T05:00:00+00:00", "from": "Sam", "status": "Read"},
        {"id": "m2", "body": "", "timestamp": "2020-08-02T12:00:00+00:00", "from": "Sam", "status": "Read"},
        {"id": "m3", "body": "no timestamp here", "timestamp": "", "from": "Sam", "status": "Read"},
    ]
    return {
        "rel_path": "Forensics/phone.ufdr",
        "owners": ["Me (+15550100000)"],
        "chats": [
            {"id": "c1", "title": "Sam", "source": "WhatsApp", "participants": ["Sam", "Me"],
             "start": msgs[0]["timestamp"], "last_activity": msgs[2]["timestamp"], "message_count": 4,
             "preview": "meet at the park", "messages": msgs},
            {"id": "c2", "title": "Empty", "source": "Native Messages", "participants": [], "message_count": 0,
             "messages": []},
        ],
    }


def test_dates_use_case_timezone():
    p = _payload()
    ny = read_messages(p, p["chats"][0], date_from="2020-07-31", date_to="2020-07-31", case_tz="America/New_York")
    assert [m["index"] for m in ny["messages"]] == [0]
    assert ny["messages"][0]["local_time"].startswith("2020-07-31 23:30:00")
    utc = read_messages(p, p["chats"][0], date_from="2020-08-01", date_to="8/1/2020")
    assert [m["index"] for m in utc["messages"]] == [0, 1]
    assert utc["filters"]["timezone"].startswith("UTC")
    with pytest.raises(ValueError):
        read_messages(p, p["chats"][0], tz="Mars/Olympus")
    with pytest.raises(ValueError):
        read_messages(p, p["chats"][0], date_from="2020-08-02", date_to="2020-08-01")


def test_list_chats_filters_and_counts():
    p = _payload()
    out = list_chats([p])
    assert [c["chat_id"] for c in out["chats"]] == ["c1"]  # empty thread hidden by default
    assert len(list_chats([p], include_empty=True)["chats"]) == 2
    assert list_chats([p], app="whatsapp")["total"] == 1
    assert list_chats([p], app="instagram")["total"] == 0
    rng = list_chats([p], date_from="2020-08-02", date_to="2020-08-02")
    assert rng["chats"][0]["messages_in_range"] == 1


def test_search_modes():
    p = _payload()
    assert [h["index"] for h in search_messages([p], "PARK meet")["hits"]] == [0]
    assert search_messages([p], '"park meet"')["total"] == 0
    assert search_messages([p], '"the park"')["total"] == 1
    ph = search_messages([p], "(555) 010-0042")
    assert ph["match_mode"] == "phone digits" and ph["hits"][0]["index"] == 1
    with pytest.raises(ValueError):
        search_messages([p], "  ")


def test_paging_is_capped_and_says_so(monkeypatch):
    p = _payload()
    many = [dict(p["chats"][0]["messages"][0], id=f"x{i}") for i in range(700)]
    chat = dict(p["chats"][0], messages=many, message_count=700)
    monkeypatch.setattr(mcp_chats, "RESPONSE_BUDGET_CHARS", 10_000_000)  # isolate the count cap
    out = read_messages(p, chat, limit=5000)
    assert out["returned"] == mcp_chats.MAX_MESSAGES and out["truncated"]
    assert "capped at 300" in out["notice"] and f"offset={mcp_chats.MAX_MESSAGES}" in out["notice"]
    last = read_messages(p, chat, limit=300, offset=600)
    assert last["returned"] == 100 and not last["truncated"] and last["notice"] is None
    monkeypatch.setattr(mcp_chats, "RESPONSE_BUDGET_CHARS", 1000)
    small = read_messages(p, chat, limit=50)
    assert 0 < small["returned"] < 50 and "budget" in small["notice"]


def test_get_file_cap_and_unindexed_messages(tmp_path):
    from mcp import Client

    from marshal_oss.config import get_settings
    from marshal_oss.db import Database
    from marshal_oss.embed import Embedder
    from marshal_oss.server import build_mcp_server

    root = tmp_path / "files"
    (root / "a").mkdir(parents=True)
    (root / "a" / "phone.ufdr").write_bytes(b"x")
    (root / "a" / "prod.zip").write_bytes(b"x")
    db = Database(tmp_path / "t.db")
    case = db.upsert_case("demo", "Demo", str(root))
    big = "word " * 20_000  # 100,000 chars
    fid = db.upsert_file(case.id, "a/big.txt", "big.txt", None, "text/plain", len(big), None, None,
                         status="ready", meta={})
    db.replace_chunks(fid, case.id, "a/big.txt", [{"text": big.strip()}])
    mcp = build_mcp_server(get_settings(), db, Embedder(get_settings()))

    async def run():
        async with Client(mcp) as c:
            r_u = await c.call_tool("get_file", {"path": "a/phone.ufdr", "case_id": case.id})
            r_z = await c.call_tool("get_file", {"path": "a/prod.zip", "case_id": case.id})
            r_d = await c.call_tool("get_file", {"path": "a", "case_id": case.id})
            r_b = await c.call_tool("get_file", {"path": "a/big.txt", "case_id": case.id, "max_chars": 0})
            return r_u, r_z, r_d, r_b

    r_u, r_z, r_d, r_b = asyncio.run(run())
    assert r_u.is_error and "Cellebrite phone extraction" in r_u.content[0].text and "cellebrite_chats" in r_u.content[0].text
    assert r_z.is_error and "zip archive" in r_z.content[0].text
    assert r_d.is_error and "is a folder" in r_d.content[0].text
    data = r_b.structured_content
    data = data.get("result", data)
    assert data["text_total_chars"] == 99_999
    assert len(data["text"]) == 40_000 and data["truncated"] and data["next_offset_chars"] == 40_000
    assert "offset_chars=40000" in data["notice"] and "capped" in data["notice"]
