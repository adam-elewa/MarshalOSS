import asyncio

from marshal_oss.forensics_ufdr import parse_att_report_au

ATT_AU = """      Voice Usage For: (555)010-0000
      Item,ConnDateTime(UTC),SeizureTime,ET,OriginatingNumber,TerminatingNumber,IMEI,IMSI,CT,Feature,DIALED,FORWARDED,TRANSLATED,ORIG_ORIG,MAKE,MODEL,CellLocation
      1,07/15/20 06:44:59,0:28,0:00,15550100000,15550100001,1,2,MO,[],,,,,APPLE,IPHONE11,[207842313:811884:-74.505136:40.459689:210:-1.0]
      2,07/15/20 07:01:58,0:03,0:03,15550100000,15550100002,1,2,MO,[VCORR],,,,,APPLE,IPHONE11,[207842313:811884:-74.505136:40.459689:210:-1.0]
      3,07/15/20 07:15:59,0:27,0:00,15550100003,15550100000,1,2,MT,[NIOP:CFNA:VM],,,,,APPLE,IPHONE11,[208934168:816149:-74.585056:40.393408:250:-1.0]
      4,07/15/20 07:20:00,0:10,0:00,15550100003,15550100000,,,MT,[NIOP],,,,,,,
      5,07/15/20 07:30:00,0:10,0:00,15550100003,15550100000,1,2,MO,[Wi-Fi],,,,,APPLE,IPHONE11,[000000001:0:0.0:0.0:-1:-1.0]
"""


def test_att_report_au_keeps_rows_with_feature_codes(tmp_path):
    p = tmp_path / "ReportAU_1.txt"
    p.write_text(ATT_AU)
    events = parse_att_report_au(p)
    # rows 1-3 have a CellLocation; 4 has none; 5 is AT&T's 0,0 placeholder
    assert [e["when"] for e in events] == [
        "2020-07-15 06:44:59",
        "2020-07-15 07:01:58",
        "2020-07-15 07:15:59",
    ]
    assert events[2]["lat"] == 40.393408 and events[2]["lon"] == -74.585056
    assert events[2]["azimuth"] == 250


def test_mcp_tool_errors_reach_client(tmp_path, monkeypatch):
    from mcp import Client

    from marshal_oss.config import get_settings
    from marshal_oss.db import Database
    from marshal_oss.embed import Embedder
    from marshal_oss.server import build_mcp_server

    settings = get_settings()
    db = Database(tmp_path / "t.db")
    (tmp_path / "files").mkdir()
    case = db.upsert_case("demo", "Demo", str(tmp_path / "files"))
    mcp = build_mcp_server(settings, db, Embedder(settings))

    async def run():
        async with Client(mcp) as c:
            r1 = await c.call_tool("get_file", {"path": "nope.pdf", "case_id": case.id})
            r2 = await c.call_tool("get_file", {"path": "../../etc/passwd", "case_id": case.id})
            r3 = await c.call_tool("search", {"query": "x", "case_id": 999999})
            return [r.content[0].text for r in (r1, r2, r3)]

    t1, t2, t3 = asyncio.run(run())
    assert "file not found: nope.pdf" in t1
    assert "path escapes case root" in t2
    assert "case not found: 999999" in t3
