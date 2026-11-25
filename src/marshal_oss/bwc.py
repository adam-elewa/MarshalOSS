"""Bodycam / dashcam multi-cam index for the defense viewer.

Projects ingest media metadata + timed speech/visual segments into the legacy
BWC viewer contract (see web/bwc/). Officer names are not in Axon export
filenames; we label by device + serial and honor an optional meta override.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .db import Case, Database
from .search import encode_rel_path

_AXON_BODY = re.compile(
    r"(?i)^axon[_ ]body[_ ]?(\d+)[_ ]video[_ ](\d{4}-\d{2}-\d{2})[_ ](\d{2})(\d{2})(?:[_ ]([A-Z0-9]{6,}))?"
)
_AXON_FLEET = re.compile(
    r"(?i)^axon[_ ]fleet[_ ]?(\d+)(?:[_ ](ir))?[_ ]video[_ ](\d{4}-\d{2}-\d{2})[_ ](\d{2})(\d{2})"
)
_OFFICER_CLIP = re.compile(r"^(.+?)\s*\((\d+)\)\s*$")


def _parse_meta(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}
    return {}


def normalize_recorded_start(value: str | None) -> str | None:
    """Return YYYY-MM-DDTHH:MM:SS for the viewer timeline parser."""
    if not value:
        return None
    s = str(value).strip().replace(" ", "T")
    # Drop timezone / fractional seconds for the UTC-as-local viewer clock
    s = re.sub(r"(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", "", s)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$", s)
    if not m:
        return None
    date, hh, mm, ss = m.group(1), m.group(2), m.group(3), m.group(4) or "00"
    return f"{date}T{hh}:{mm}:{ss}"


def camera_officer_label(name: str, media: dict[str, Any]) -> str:
    """Stable lane label. Prefer explicit officer override; else device+serial."""
    override = (media.get("officer") or media.get("officer_name") or "").strip()
    if override:
        return override

    # Washington-style production renames: "Det Egan (1) - confidential.mp4"
    base = re.sub(r"\.[^.]+$", "", name or "")
    base = re.sub(r"\s*-\s*confidential\s*$", "", base, flags=re.I).strip()
    m = _OFFICER_CLIP.match(base)
    if m and not _AXON_BODY.match(name) and not _AXON_FLEET.match(name):
        label = m.group(1).strip().replace("Po ", "PO ", 1)
        if label and not label.lower().startswith("axon"):
            return label

    kind = (media.get("kind") or "").lower()
    device = (media.get("device") or "").strip()
    serial = (media.get("device_serial") or media.get("serial") or "").strip()

    if kind == "bodycam":
        if device and serial:
            return f"{device} · {serial}"
        if serial:
            return f"Bodycam · {serial}"
        return device or "Bodycam"

    if kind == "dashcam":
        return device or "Dashcam"

    if kind == "audio":
        return "911 / Radio"

    return device or base or "Camera"


def clip_label_for(name: str, media: dict[str, Any], recorded_start: str | None) -> str:
    override = (media.get("clip_label") or "").strip()
    if override:
        return override
    base = re.sub(r"\.[^.]+$", "", name or "")
    m = _OFFICER_CLIP.match(re.sub(r"\s*-\s*confidential\s*$", "", base, flags=re.I).strip())
    if m and not _AXON_BODY.match(name) and not _AXON_FLEET.match(name):
        return f"Clip {m.group(2)}"
    if recorded_start and len(recorded_start) >= 16:
        try:
            dt = datetime.strptime(recorded_start, "%Y-%m-%dT%H:%M:%S")
            h = dt.hour % 12 or 12
            return f"{h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"
        except Exception:
            return recorded_start[11:16]
    return "Recording"


def _seg_type(kind: str) -> str | None:
    k = (kind or "").lower()
    if k in ("speech", "transcript", "asr"):
        return "transcript"
    if k in ("visual", "scene", "frame", "vision"):
        return "scene"
    return None


def build_bwc_index(db: Database, case: Case) -> dict[str, Any]:
    """Build the multi-cam viewer index for a case from ingest tables."""
    files = db.list_files_with_meta(case.id)
    videos: list[dict[str, Any]] = []
    total_segments = 0
    audio_count = 0
    video_count = 0

    for f in files:
        if (f.get("status") or "") != "ready":
            continue
        meta = f.get("meta") or {}
        media = meta.get("media") if isinstance(meta.get("media"), dict) else {}
        kind = (media.get("kind") or "").lower()
        mime = (f.get("mime") or "").lower()
        name = f.get("name") or ""
        rel = f.get("rel_path") or ""

        lower_name = name.lower()
        looks_like_av_file = lower_name.endswith(
            (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".wmv", ".wav", ".mp3", ".m4a", ".aac")
        )
        is_av = kind in ("bodycam", "dashcam", "audio", "video") or mime.startswith(
            ("video/", "audio/")
        )
        if not is_av:
            # Axon path heuristic if media.kind missing (must be a real media file)
            if not looks_like_av_file:
                continue
            if not (_AXON_BODY.match(name) or _AXON_FLEET.match(name)):
                continue
            kind = "bodycam" if _AXON_BODY.match(name) else "dashcam"
            media = {**media, "kind": kind}
        elif kind in ("bodycam", "dashcam", "video") and not (
            looks_like_av_file or mime.startswith("video/")
        ):
            # e.g. AXON_…mp4_Error.txt matching a filename prefix
            continue

        if kind == "video":
            # Unclassified video — include only if it has timed segments
            segs_probe = db.list_media_segments(int(f["id"]))
            if not segs_probe:
                continue

        if kind == "audio":
            # Keep 911/radio out of multi-cam timeline (viewer filters audio);
            # still useful if we later add an audio rail — skip for now.
            audio_count += 1
            continue

        recorded = normalize_recorded_start(media.get("recorded_start"))
        duration = media.get("duration_sec")
        try:
            duration_sec = float(duration) if duration is not None else 0.0
        except (TypeError, ValueError):
            duration_sec = 0.0

        raw_segs = db.list_media_segments(int(f["id"]))
        segments: list[dict[str, Any]] = []
        for s in raw_segs:
            st = _seg_type(s.get("kind") or "")
            if not st:
                continue
            text = (s.get("text") or "").strip()
            if not text:
                continue
            item: dict[str, Any] = {
                "start": float(s.get("start_sec") or 0),
                "end": float(s.get("end_sec") or s.get("start_sec") or 0),
                "type": st,
                "text": text,
            }
            if s.get("speaker"):
                item["speaker"] = s["speaker"]
            segments.append(item)

        total_segments += len(segments)
        video_count += 1
        officer = camera_officer_label(name, media)
        clip = clip_label_for(name, media, recorded)
        enc = encode_rel_path(rel)
        media_url = f"/files/{case.slug}/{enc}"

        title_bits = [officer, clip]
        videos.append(
            {
                "id": f"f{f['id']}",
                "file_id": f["id"],
                "filename": name,
                "title": " — ".join(title_bits),
                "folder": rel.rsplit("/", 1)[0] if "/" in rel else "",
                "kind": "video",
                "camera_kind": kind or media.get("kind") or "video",
                "media": media_url,
                "duration_sec": duration_sec,
                "recorded_start": recorded,
                "device": media.get("device"),
                "device_serial": media.get("device_serial") or media.get("serial"),
                "officer": officer,
                "clipLabel": clip,
                "segment_count": len(segments),
                "segments": segments,
            }
        )

    videos.sort(
        key=lambda v: (
            v.get("recorded_start") or "9999",
            v.get("officer") or "",
            v.get("filename") or "",
        )
    )

    return {
        "case": case.display_name or case.slug,
        "case_id": case.id,
        "slug": case.slug,
        "title": f"{case.display_name or case.slug} — bodycam / dashcam",
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "video_count": video_count,
        "audio_count": audio_count,
        "total_segments": total_segments,
        "videos": videos,
        "officer_naming": {
            "mode": "device_serial",
            "note": (
                "Axon exports use device serials, not officer names. "
                "Use Label in the viewer (or set meta.media.officer) to name a camera lane. "
                "Renamed files like 'Det Egan (1).mp4' are parsed when present."
            ),
        },
    }


_GENERIC_LABELS = {
    "officer",
    "police officer",
    "police vehicle",
    "state police",
    "axon fleet",
    "axon body 3",
    "axon body",
    "speaker a",
    "speaker b",
    "speaker c",
    "speaker d",
    "speaker e",
    "speaker f",
}


def _usable_label_suggestion(name: str) -> bool:
    n = name.strip()
    if len(n) < 3 or len(n) > 80:
        return False
    if n.casefold() in _GENERIC_LABELS:
        return False
    if n.casefold().startswith("speaker "):
        return False
    # Prefer real names / titles over scene phrases
    if " wearing " in n.casefold() or n.casefold().startswith(("male ", "man ", "the ")):
        return False
    return True


def label_suggestions(db: Database, case: Case, *, limit: int = 80) -> dict[str, Any]:
    """Person/officer entities for the BWC label combobox."""
    stats = db.case_entity_tag_stats(case.id, top_n=max(limit, 100))
    by_type = stats.get("entities_by_type") or {}
    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for et in ("officer", "person"):
        for row in by_type.get(et) or []:
            name = str(row.get("canonical") or "").strip()
            if not name or not _usable_label_suggestion(name):
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(
                {
                    "label": name,
                    "type": et,
                    "count": int(row.get("count") or 0),
                }
            )
            if len(suggestions) >= limit:
                break
        if len(suggestions) >= limit:
            break
    return {"case_id": case.id, "suggestions": suggestions}


def _set_media_officer(db: Database, case_id: int, file_id: int, label: str | None) -> bool:
    row = db.conn.execute(
        "SELECT id, meta FROM files WHERE case_id=? AND id=?",
        (case_id, file_id),
    ).fetchone()
    if not row:
        return False
    meta = _parse_meta(row["meta"])
    media = meta.get("media") if isinstance(meta.get("media"), dict) else {}
    media = dict(media)
    if label:
        media["officer"] = label
        media["officer_source"] = "user"
    else:
        media.pop("officer", None)
        media.pop("officer_source", None)
    meta["media"] = media
    db.conn.execute(
        "UPDATE files SET meta=? WHERE id=?",
        (json.dumps(meta, ensure_ascii=False), file_id),
    )
    return True


def apply_camera_label(
    db: Database,
    case: Case,
    *,
    file_id: int,
    label: str | None,
    scope: str = "camera",
) -> dict[str, Any]:
    """Set or clear meta.media.officer for one clip or all clips from that camera."""
    scope = (scope or "camera").strip().lower()
    if scope not in ("camera", "clip"):
        raise ValueError("scope must be camera|clip")
    label_clean = (label or "").strip() or None

    row = db.conn.execute(
        "SELECT id, name, meta FROM files WHERE case_id=? AND id=?",
        (case.id, file_id),
    ).fetchone()
    if not row:
        raise ValueError("file not found")
    meta = _parse_meta(row["meta"])
    media = meta.get("media") if isinstance(meta.get("media"), dict) else {}
    serial = (media.get("device_serial") or media.get("serial") or "").strip()
    device = (media.get("device") or "").strip()
    kind = (media.get("kind") or "").strip().lower()

    target_ids = [int(row["id"])]
    if scope == "camera":
        target_ids = []
        for f in db.list_files_with_meta(case.id):
            if (f.get("status") or "") != "ready":
                continue
            m = f.get("meta") or {}
            med = m.get("media") if isinstance(m.get("media"), dict) else {}
            if not isinstance(med, dict):
                continue
            if serial:
                other = (med.get("device_serial") or med.get("serial") or "").strip()
                if other and other == serial:
                    target_ids.append(int(f["id"]))
            elif device:
                if (med.get("device") or "").strip() == device and (
                    not kind or (med.get("kind") or "").strip().lower() == kind
                ):
                    target_ids.append(int(f["id"]))
            elif int(f["id"]) == file_id:
                target_ids.append(int(f["id"]))
        if file_id not in target_ids:
            target_ids.append(file_id)

    updated = 0
    for fid in sorted(set(target_ids)):
        if _set_media_officer(db, case.id, fid, label_clean):
            updated += 1
    db.conn.commit()
    return {
        "ok": True,
        "case_id": case.id,
        "file_id": file_id,
        "label": label_clean,
        "scope": scope,
        "updated": updated,
        "serial": serial or None,
        "device": device or None,
    }
