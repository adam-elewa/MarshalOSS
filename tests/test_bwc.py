"""Bodycam index projection."""

from __future__ import annotations

from marshal_oss.bwc import (
    apply_camera_label,
    build_bwc_index,
    camera_officer_label,
    clip_label_for,
    label_suggestions,
    normalize_recorded_start,
)
from marshal_oss.db import Database


def test_normalize_recorded_start_pads_seconds():
    assert normalize_recorded_start("2024-08-09T15:05") == "2024-08-09T15:05:00"
    assert normalize_recorded_start("2024-08-09T15:05:07") == "2024-08-09T15:05:07"
    assert normalize_recorded_start(None) is None


def test_camera_label_uses_serial_not_axon_filename():
    media = {
        "kind": "bodycam",
        "device": "Axon Body 3",
        "device_serial": "X60AA001A",
    }
    assert (
        camera_officer_label("Axon_Body_3_Video_2024-08-09_1505_X60AA001A.mp4", media)
        == "Axon Body 3 · X60AA001A"
    )


def test_camera_label_honors_officer_override():
    media = {"kind": "bodycam", "device_serial": "X1", "officer": "Tpr. Baka"}
    assert camera_officer_label("Axon_Body_3_Video_x.mp4", media) == "Tpr. Baka"


def test_camera_label_parses_officer_named_renames():
    media = {"kind": "video"}
    assert camera_officer_label("Det Smith (1) - confidential.mp4", media) == "Det Smith"
    assert clip_label_for("Det Smith (1) - confidential.mp4", media, None) == "Clip 1"


def test_build_bwc_index_maps_segments(tmp_path):
    db = Database(tmp_path / "t.db")
    case = db.upsert_case("doe", "Jane Doe", str(tmp_path / "files"))
    meta = {
        "media": {
            "kind": "bodycam",
            "device": "Axon Body 3",
            "device_serial": "X60AA001A",
            "recorded_start": "2024-08-09T15:05",
            "duration_sec": 120.0,
        }
    }
    path = "Axon_Body_3_Video_2024-08-09_1505_X60AA001A.mp4"
    fid = db.upsert_file(
        case.id, path, path, None, "video/mp4", 10, None, "x", status="ready", meta=meta
    )
    db.conn.execute(
        """
        INSERT INTO media_segments(file_id, case_id, seg_index, kind, start_sec, end_sec, text, speaker)
        VALUES (?, ?, 0, 'speech', 1.0, 2.0, 'hello waistband', 'A'),
               (?, ?, 1, 'visual', 0.0, 5.0, 'roadway scene', NULL)
        """,
        (fid, case.id, fid, case.id),
    )
    db.conn.commit()

    idx = build_bwc_index(db, case)
    assert idx["video_count"] == 1
    assert idx["total_segments"] == 2
    v = idx["videos"][0]
    assert v["officer"] == "Axon Body 3 · X60AA001A"
    assert v["recorded_start"] == "2024-08-09T15:05:00"
    assert v["media"].startswith("/files/doe/")
    types = {s["type"] for s in v["segments"]}
    assert types == {"transcript", "scene"}


def test_generic_video_without_time_cue_excluded(tmp_path):
    db = Database(tmp_path / "t.db")
    case = db.upsert_case("doe", "Jane Doe", str(tmp_path / "files"))
    # Unclassified video with timed segments but no recorded_start → excluded
    fid = db.upsert_file(
        case.id,
        "hallway_cam.mp4",
        "hallway_cam.mp4",
        None,
        "video/mp4",
        10,
        None,
        "g1",
        status="ready",
        meta={"media": {"kind": "video", "duration_sec": 60.0}},
    )
    db.conn.execute(
        """
        INSERT INTO media_segments(file_id, case_id, seg_index, kind, start_sec, end_sec, text, speaker)
        VALUES (?, ?, 0, 'speech', 1.0, 2.0, 'hello', 'A')
        """,
        (fid, case.id),
    )
    # Bodycam missing recorded_start → excluded
    db.upsert_file(
        case.id,
        "Axon_Body_3_Video_no_time_X60AA001A.mp4",
        "Axon_Body_3_Video_no_time_X60AA001A.mp4",
        None,
        "video/mp4",
        10,
        None,
        "g2",
        status="ready",
        meta={
            "media": {
                "kind": "bodycam",
                "device": "Axon Body 3",
                "device_serial": "X60AA001A",
                "duration_sec": 60.0,
            }
        },
    )
    db.conn.commit()
    idx = build_bwc_index(db, case)
    assert idx["video_count"] == 0
    assert idx["videos"] == []


def test_apply_camera_label_by_serial(tmp_path):
    db = Database(tmp_path / "t.db")
    case = db.upsert_case("doe", "Jane Doe", str(tmp_path / "files"))
    ids = []
    for i, start in enumerate(("1505", "2117")):
        path = f"Axon_Body_3_Video_2024-08-09_{start}_X60AA001A.mp4"
        meta = {
            "media": {
                "kind": "bodycam",
                "device": "Axon Body 3",
                "device_serial": "X60AA001A",
                "recorded_start": f"2024-08-09T{start[:2]}:{start[2:]}",
                "duration_sec": 10.0,
            }
        }
        ids.append(
            db.upsert_file(
                case.id, path, path, None, "video/mp4", 10, None, f"h{i}", status="ready", meta=meta
            )
        )
    other = db.upsert_file(
        case.id,
        "Axon_Body_3_Video_2024-08-09_1519_X60AB002B.mp4",
        "Axon_Body_3_Video_2024-08-09_1519_X60AB002B.mp4",
        None,
        "video/mp4",
        10,
        None,
        "other",
        status="ready",
        meta={
            "media": {
                "kind": "bodycam",
                "device": "Axon Body 3",
                "device_serial": "X60AB002B",
                "recorded_start": "2024-08-09T15:19",
                "duration_sec": 10.0,
            }
        },
    )
    result = apply_camera_label(db, case, file_id=ids[0], label="Tpr. Baka", scope="camera")
    assert result["updated"] == 2
    idx = build_bwc_index(db, case)
    by_id = {v["file_id"]: v["officer"] for v in idx["videos"]}
    assert by_id[ids[0]] == "Tpr. Baka"
    assert by_id[ids[1]] == "Tpr. Baka"
    assert by_id[other] == "Axon Body 3 · X60AB002B"

    cleared = apply_camera_label(db, case, file_id=ids[0], label=None, scope="camera")
    assert cleared["updated"] == 2
    idx2 = build_bwc_index(db, case)
    assert {v["officer"] for v in idx2["videos"] if v["file_id"] in ids} == {
        "Axon Body 3 · X60AA001A"
    }

    # suggestions include person entities when present
    db.conn.execute(
        "UPDATE files SET meta=? WHERE id=?",
        (
            '{"entities":[{"type":"officer","canonical":"Jamaal Hart"}],'
            '"media":{"kind":"bodycam","device":"Axon Body 3","device_serial":"X60AB002B",'
            '"recorded_start":"2024-08-09T15:19","duration_sec":10}}',
            other,
        ),
    )
    db.conn.commit()
    sug = label_suggestions(db, case)
    assert any(s["label"] == "Jamaal Hart" for s in sug["suggestions"])


def test_timeline_offsets_persist_per_case(tmp_path):
    db = Database(tmp_path / "t.db")
    a = db.upsert_case("a", "A", str(tmp_path / "a"))
    b = db.upsert_case("b", "B", str(tmp_path / "b"))
    saved = db.set_bwc_timeline_offsets(a.id, {"f1": 12.4, "f2": 0, "f3": "bad", "f4": 10**9})
    assert saved == {"f1": 12, "f4": 7 * 24 * 3600}
    assert db.get_bwc_timeline_offsets(a.id) == saved
    assert db.get_bwc_timeline_offsets(b.id) == {}
