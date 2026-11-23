"""Heuristic document categories for Document Explorer browse."""

from __future__ import annotations

import re
from typing import Any

# Stable ids used in URLs / API. Order = priority (first match wins).
CATEGORY_DEFS: list[tuple[str, str]] = [
    ("criminal_history", "Criminal History"),
    ("police_reports", "Police Reports"),
    ("dui_traffic", "DUI / Traffic"),
    ("court", "Court & Supervision"),
    ("forensic", "Forensic & Ballistics"),
    ("bodycam", "Bodycam"),
    ("dashcam", "Dashcam"),
    ("audio", "Audio & 911"),
    ("photos", "Photos & Maps"),
    ("other", "Other"),
]

CATEGORY_LABELS = {cid: label for cid, label in CATEGORY_DEFS}


def _norm_tags(tags: Any) -> set[str]:
    out: set[str] = set()
    if not tags:
        return out
    for t in tags:
        if isinstance(t, str):
            label = t
        elif isinstance(t, dict):
            label = str(t.get("name") or t.get("label") or t.get("tag") or "")
        else:
            label = str(t)
        label = label.strip().lower().replace(" ", "_")
        if label:
            out.add(label)
    return out


def categorize_document(
    *,
    name: str,
    rel_path: str = "",
    mime: str | None = None,
    tags: Any = None,
    ext: str | None = None,
) -> str:
    """Return a category id for a document (single primary category)."""
    name_l = (name or "").lower()
    path_l = (rel_path or "").lower()
    blob = f"{name_l} {path_l}"
    mime_l = (mime or "").lower()
    ext_l = (ext or "").lower()
    if not ext_l and "." in name_l:
        ext_l = name_l[name_l.rfind(".") :]
    tagset = _norm_tags(tags)

    def has(*needles: str) -> bool:
        return any(n in tagset for n in needles)

    def name_has(*needles: str) -> bool:
        return any(n in blob for n in needles)

    is_video = mime_l.startswith("video/") or ext_l in {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
    is_audio = mime_l.startswith("audio/") or ext_l in {".wav", ".mp3", ".m4a", ".aac"}
    is_image = mime_l.startswith("image/") or ext_l in {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".kml",
    }

    # Media types first (by file type, not incidental tags on PDFs)
    if is_video and (
        "bodycam" in tagset
        or name_has("axon_body", "body_3", "bodycam", "body worn", "bwc")
        or re.search(r"\bbody[_\s-]?cam", blob)
        or (name_has("axon") and name_has("body"))
    ):
        return "bodycam"
    if is_video and (
        "dashcam" in tagset
        or name_has("axon_fleet", "dashcam", "fleet_2", "fleet 2", "fleet")
        or "police_vehicle" in tagset
    ):
        return "dashcam"
    if is_audio or has("911_call", "emergency_call") or name_has("_911_", "911_"):
        return "audio"
    if is_image:
        return "photos"

    # Document categories (narrow → broad)
    if (
        has("criminal_history", "aliases", "dispositions", "prior_convictions")
        or name_has("criminal history", "rap sheet")
        or re.search(r"(^|[^a-z])cch([^a-z]|$)", blob)
    ):
        return "criminal_history"

    if (
        has("ballistics", "forensic", "ammunition")
        or name_has("ballistic", "forensic", "lab report")
    ):
        return "forensic"

    if (
        has(
            "dui",
            "dwi",
            "driving_under_influence",
            "breath_test_refusal",
            "open_container",
            "impaired_driving",
            "dwi_discovery",
            "dwi_charge",
            "accident_report",
            "motor_vehicle_accident",
        )
        or re.search(r"(^|[^a-z])(dui|dwi)([^a-z]|$)", blob)
        or name_has(
            "mv report",
            "motor vehicle",
            "impound",
            "vehicle report",
            "accident report",
            "dui discovery",
            "dui report",
        )
    ):
        return "dui_traffic"

    if (
        has("arrest_report", "police_report", "arrest_card", "investigation_report", "incident_report")
        or name_has(
            "arrest report",
            "arrest_report",
            "investigation report",
            "investigation_report",
            "police report",
            "incident report",
            "arrest card",
            "arrest_card",
        )
        or re.search(r"\b(arrest|investigation)\b", name_l)
    ):
        return "police_reports"

    if (
        has(
            "court_order",
            "supervised_release",
            "court_records",
            "court_case",
            "bail_hearing",
            "summons",
            "complaint",
        )
        or re.search(r"(^|[^a-z])(psr|12cd)([^a-z]|$)", blob)
        or name_has("complaint", "judgment", "indictment", "detention order", "bail")
    ):
        return "court"

    # leftover videos without clear body/dash cues
    if is_video:
        return "other"

    return "other"


def categorize_file_row(row: dict[str, Any]) -> str:
    tags = row.get("tags")
    if tags is None and isinstance(row.get("meta"), dict):
        tags = row["meta"].get("tags")
    return categorize_document(
        name=str(row.get("name") or ""),
        rel_path=str(row.get("rel_path") or row.get("path") or ""),
        mime=row.get("mime"),
        tags=tags,
        ext=row.get("ext"),
    )
