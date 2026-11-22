import json
import zipfile
from pathlib import Path

from marshal_oss.archives import EXTRACT_SUFFIX
from marshal_oss.config import Settings
from marshal_oss.db import Database
from marshal_oss.hashing import sniff_ext
from marshal_oss.manifest import prepare_production


def test_prepare_unzips_and_manifest(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    pdf = tmp_path / "docs" / "PROD_00001234.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    zpath = tmp_path / "radio.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("clip.wav", b"RIFF$$$$WAVEfmt ")
        zf.writestr("PROD_00001590", b"\xff\xfb\x90d" + b"\x00" * 32 + b"Xing")

    settings = Settings(expand_zips=True, expand_zip_max_depth=1)
    db = Database(tmp_path / "test.db")
    result = prepare_production(
        tmp_path,
        settings,
        db=db,
        slug="demo-case",
        display_name="Demo Case",
        expand=True,
    )

    assert result.summary["file_count"] >= 3
    assert (tmp_path / f"radio{EXTRACT_SUFFIX}").is_dir()
    assert Path(result.jsonl_path).is_file()
    assert Path(result.summary_path).is_file()

    summary = json.loads(Path(result.summary_path).read_text())
    assert summary["schema"] == "marshaloss.production_manifest.v1"
    assert summary["ingestible_count"] >= 1

    case = db.get_case_by_slug("demo-case")
    assert case is not None
    rows = db.list_production_manifest(case.id)
    assert len(rows) == result.summary["file_count"]
    assert any(r["sha256"] for r in rows)

    bare = next(r for r in rows if r["name"] == "PROD_00001590")
    assert bare["detected_ext"] == ".mp3"
    assert bare["produced_from"] == "zip_expand"
    assert bare["parent_zip"] and bare["parent_zip"].endswith("radio.zip")


def test_sniff_pdf_and_mp3(tmp_path: Path):
    p = tmp_path / "x"
    p.write_bytes(b"%PDF-1.7")
    assert sniff_ext(p) == ".pdf"
    p.write_bytes(b"\xff\xfb\x90d" + b"\x00" * 40)
    assert sniff_ext(p) == ".mp3"
