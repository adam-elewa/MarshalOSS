import zipfile
from pathlib import Path

from marshal_oss.archives import EXTRACT_SUFFIX, expand_zips


def test_expand_zip_beside_archive(tmp_path: Path):
    zpath = tmp_path / "ProdA.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("docs/a.txt", "hello discovery")
        zf.writestr("__MACOSX/._junk", "skip")
        zf.writestr("docs/.DS_Store", "skip")

    stats = expand_zips(tmp_path, max_depth=1)
    assert stats["archives"] == 1
    assert stats["extracted"] >= 1
    dest = tmp_path / f"ProdA{EXTRACT_SUFFIX}"
    assert (dest / "docs" / "a.txt").read_text() == "hello discovery"
    assert not (dest / "__MACOSX").exists()

    # Idempotent
    stats2 = expand_zips(tmp_path, max_depth=1)
    assert stats2["skipped"] >= 1
    assert stats2["archives"] == 0


def test_zip_slip_rejected(tmp_path: Path):
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("../outside.txt", "nope")
    stats = expand_zips(tmp_path, max_depth=1)
    assert stats["errors"] == 1
    assert not (tmp_path / "outside.txt").exists()
