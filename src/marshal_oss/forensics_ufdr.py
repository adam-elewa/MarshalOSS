"""Cellebrite UFDR chat extraction via ufedlib-py (Reader-equivalent decoded chats)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_CSLI_NAME = re.compile(
    r"(csli|cell[\s_-]?site|tower[\s_-]?dump|\bcdr\b|neustar|call[\s_-]?detail|location[\s_-]?history)",
    re.I,
)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
_UUID_PAREN_RE = re.compile(
    r"\s*\(([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\)\s*$",
    re.I,
)
_OTHER_RE = re.compile(r"^_\$!<Other>!\$_\s*(?:\((.+)\))?\s*$", re.I)
_LONG_ID_RE = re.compile(r"^\d{15,}$")
_PHONE_RE = re.compile(r"(\+?\d[\d\-\s()]{6,}\d)")
_TRAILING_ID_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<id>\+?\d[\d\s\-]*)\)\s*$")
_CACHE_VER = "v3"


def ufedlib_available() -> bool:
    try:
        import ufedlib_py  # noqa: F401

        return True
    except ImportError:
        return False


def find_ufdr_files(files_root: str | Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Locate .ufdr packages under a case root."""
    root = Path(files_root)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    try:
        for p in sorted(root.rglob("*.ufdr")):
            if not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")
            out.append(
                {
                    "rel_path": rel,
                    "name": p.name,
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                }
            )
            if len(out) >= limit:
                break
    except OSError as e:
        log.warning("UFDR scan failed under %s: %s", root, e)
    return out


def _cache_dir(files_root: str | Path) -> Path:
    d = Path(files_root) / ".marshaloss" / "forensics" / "ufdr"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(path: Path) -> str:
    st = path.stat()
    raw = f"{_CACHE_VER}|{path.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _to_plain(obj: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return str(obj)
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _to_plain(v, depth=depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(x, depth=depth + 1) for x in obj]
    if is_dataclass(obj) and not isinstance(obj, type):
        try:
            return _to_plain(asdict(obj), depth=depth + 1)
        except Exception:
            pass
    # Attribute bag / pydantic-ish
    data: dict[str, Any] = {}
    for name in dir(obj):
        if name.startswith("_"):
            continue
        try:
            val = getattr(obj, name)
        except Exception:
            continue
        if callable(val):
            continue
        data[name] = _to_plain(val, depth=depth + 1)
    return data if data else str(obj)


def _pick(obj: Any, *names: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        for n in names:
            if n in obj and obj[n] not in (None, ""):
                return obj[n]
        # case-insensitive
        lower = {str(k).lower(): v for k, v in obj.items()}
        for n in names:
            if n.lower() in lower and lower[n.lower()] not in (None, ""):
                return lower[n.lower()]
        return default
    for n in names:
        if hasattr(obj, n):
            val = getattr(obj, n)
            if val not in (None, ""):
                return val
    return default


def _raw_party_label(party: Any) -> str:
    if party is None:
        return ""
    if isinstance(party, str):
        return party.strip()
    name = _pick(party, "Name", "name", "DisplayName", "displayName")
    ident = _pick(
        party,
        "Identifier",
        "identifier",
        "Id",
        "id",
        "PhoneNumber",
        "phoneNumber",
    )
    if name and ident and str(name) != str(ident):
        return f"{name} ({ident})"
    return str(name or ident or "").strip()


def clean_party_label(label: Any) -> str:
    """Human-readable party label (strip Cellebrite Other markers / UUID noise)."""
    s = str(label or "").strip()
    if not s:
        return ""
    other = _OTHER_RE.match(s)
    if other:
        phone = (other.group(1) or "").strip()
        return f"Me ({phone})" if phone else "Me"
    s = _UUID_PAREN_RE.sub("", s).strip()
    return s


def _is_opaque_id(label: str) -> bool:
    s = str(label or "").strip()
    if not s:
        return True
    if _UUID_RE.match(s) or _LONG_ID_RE.match(s):
        return True
    # bare uuid after cleaning
    cleaned = clean_party_label(s)
    return bool(_UUID_RE.match(cleaned) or _LONG_ID_RE.match(cleaned))


def _party_label(party: Any) -> str:
    return clean_party_label(_raw_party_label(party))


def _infer_owners(chats: list[dict[str, Any]]) -> set[str]:
    """Likely device-owner labels (per-source + global frequency, plus Me)."""
    from collections import Counter

    owners: set[str] = set()
    global_counts: Counter[str] = Counter()
    by_source: dict[str, Counter[str]] = {}
    for c in chats:
        src = str(c.get("source") or "Unknown")
        src_counts = by_source.setdefault(src, Counter())
        for p in c.get("participants") or []:
            lab = clean_party_label(p)
            if not lab:
                continue
            if lab == "Me" or lab.startswith("Me ("):
                owners.add(lab)
                continue
            global_counts[lab] += 1
            src_counts[lab] += 1
    for counts in list(by_source.values()) + [global_counts]:
        if not counts:
            continue
        top, n = counts.most_common(1)[0]
        # Dominant account in this app / overall
        if n >= 3:
            owners.add(top)
    return owners


def _short_opaque(label: str) -> str:
    s = str(label or "").strip()
    if _UUID_RE.match(s):
        return f"Contact {s[:8]}"
    if _LONG_ID_RE.match(s):
        return f"Contact …{s[-6:]}"
    return s


def _is_phone_label(label: str) -> bool:
    s = re.sub(r"[\u202a\u202c]", "", str(label or "").strip())
    if not s:
        return True
    return len(re.sub(r"[\d\s+\-().]", "", s)) == 0 and len(re.sub(r"\D", "", s)) >= 7


def _is_junk_name(label: str) -> bool:
    s = str(label or "").strip()
    if not s:
        return True
    if _OTHER_RE.match(s) or s == "Me" or s.startswith("Me ("):
        return True
    if _is_phone_label(s) or _is_opaque_id(s):
        return True
    if s.lower() in {"unknown", "untitled chat", "contact"}:
        return True
    return False


def _norm_phone(value: str) -> str:
    d = re.sub(r"\D", "", str(value or ""))
    if len(d) == 11 and d.startswith("1"):
        return d
    if len(d) == 10:
        return "1" + d
    return d


def _pretty_name(name: str) -> str:
    """Strip embedded phones / trailing app ids from a contact display name."""
    s = re.sub(r"[\u202a\u202c]", "", str(name or "").strip())
    if not s:
        return ""
    s = _PHONE_RE.sub("", s)
    s = re.sub(r"[\s,|/()\-]+$", "", s).strip()
    s = re.sub(r"^\(+|\)+$", "", s).strip()
    m = _TRAILING_ID_RE.match(s)
    if m and not _is_junk_name(m.group("name")):
        s = m.group("name").strip()
    return s


def _phone_keys(value: str) -> list[str]:
    d = _norm_phone(value)
    if len(d) < 10:
        return []
    keys = [d]
    if d.startswith("1") and len(d) == 11:
        keys.append(d[1:])
    return keys


def build_contact_directory(ufdr_path: str | Path) -> dict[str, str]:
    """Map phone digits / user ids → best human name from Contact + Party models."""
    from collections import Counter, defaultdict

    from ufedlib_py import parse_data

    scores: dict[str, Counter[str]] = defaultdict(Counter)

    def add_key(key: str, name: str, weight: int = 1) -> None:
        key = str(key or "").strip()
        pretty = _pretty_name(name)
        if not key or _is_junk_name(pretty):
            return
        if pretty == key or _is_phone_label(pretty):
            return
        scores[key][pretty] += weight

    def add_phone(raw: str, name: str, weight: int = 1) -> None:
        for k in _phone_keys(raw):
            add_key(k, name, weight)

    try:
        contacts = parse_data(str(ufdr_path), "Contact") or []
    except Exception as e:
        log.warning("Contact parse failed for %s: %s", ufdr_path, e)
        contacts = []
    if not isinstance(contacts, list):
        contacts = [contacts]

    for c in contacts:
        name = str(getattr(c, "Name", None) or "").strip()
        ctype = str(getattr(c, "Type", None) or "")
        weight = 6 if ctype == "PhoneBook" else 2
        for m in _PHONE_RE.findall(name):
            add_phone(m, name, weight + 2)
        for e in getattr(c, "Entries", None) or []:
            fields = getattr(e, "fields", None) or {}
            if not isinstance(fields, dict):
                continue
            val = str(fields.get("Value") or "").strip()
            if not val:
                continue
            digits = re.sub(r"\D", "", val)
            if 10 <= len(digits) <= 15:
                add_phone(val, name, weight + (3 if ctype == "PhoneBook" else 0))
            else:
                add_key(val, name, weight)
            eid = getattr(e, "id", None)
            if eid:
                add_key(str(eid), name, 1)

    try:
        parties = parse_data(str(ufdr_path), "Party") or []
    except Exception as e:
        log.warning("Party parse failed for %s: %s", ufdr_path, e)
        parties = []
    if not isinstance(parties, list):
        parties = [parties]

    for pt in parties:
        ident = str(getattr(pt, "Identifier", None) or "").strip()
        name = str(getattr(pt, "Name", None) or "").strip()
        if not ident or not name or _is_junk_name(name):
            continue
        add_phone(ident, name, 1)
        add_key(ident, name, 1)

    out: dict[str, str] = {}
    for key, counter in scores.items():
        best = counter.most_common(1)[0][0]
        out[key] = best
    return out


def _lookup_directory(label: str, directory: dict[str, str]) -> str | None:
    s = clean_party_label(label)
    if not s:
        return None
    for k in _phone_keys(s):
        if k in directory:
            return directory[k]
    if s in directory:
        return directory[s]
    # "Name (id)" → try id
    m = _TRAILING_ID_RE.match(s)
    if m:
        ident = m.group("id").strip()
        for k in _phone_keys(ident) + [ident, re.sub(r"\D", "", ident)]:
            if k and k in directory:
                return directory[k]
        pretty = _pretty_name(s)
        if pretty and not _is_junk_name(pretty):
            return pretty
    # UUID embedded in label
    um = _UUID_RE.search(s) or re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        s,
        re.I,
    )
    if um and um.group(0) in directory:
        return directory[um.group(0)]
    pretty = _pretty_name(s)
    if pretty and pretty != s and not _is_junk_name(pretty):
        return pretty
    return None


def _display_party(
    label: str,
    owners: set[str],
    directory: dict[str, str] | None = None,
) -> str:
    s = clean_party_label(label)
    if not s:
        return ""
    if s == "Me" or s.startswith("Me (") or s in owners:
        return "Me"
    resolved = _lookup_directory(s, directory or {})
    if resolved:
        return resolved
    if _is_opaque_id(s):
        return _short_opaque(s)
    pretty = _pretty_name(s)
    return pretty or s


def _chat_title(
    chat: dict[str, Any],
    owners: set[str],
    directory: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Return (title, handle) where handle is a phone/id subtitle when useful."""
    directory = directory or {}
    parts = [clean_party_label(p) for p in (chat.get("participants") or [])]
    parts = [p for p in parts if p]
    others = [p for p in parts if p not in owners and not (
        clean_party_label(p) == "Me" or clean_party_label(p).startswith("Me (")
    )]
    named = [p for p in others if not _is_opaque_id(p) and not _is_phone_label(p)]
    pick = named or others

    def fmt(p: str) -> tuple[str, str]:
        raw = clean_party_label(p)
        shown = _display_party(raw, owners, directory)
        handle = ""
        # Only treat true phone labels as handles (not Instagram/Snap numeric user ids)
        if _is_phone_label(raw) and shown and shown != raw and not _is_phone_label(shown):
            handle = raw
        return shown, handle

    if pick:
        pairs = [fmt(p) for p in pick]
        pairs = [(t, h) for t, h in pairs if t and t != "Me"] or [fmt(p) for p in pick]
        titles = [t for t, _ in pairs]
        handles = [h for _, h in pairs if h]
        if len(titles) == 1:
            return titles[0], (handles[0] if handles else "")
        if len(titles) <= 3:
            return ", ".join(titles), (handles[0] if len(handles) == 1 else "")
        return f"{titles[0]} +{len(titles) - 1} others", ""

    name = clean_party_label(chat.get("name") or "")
    shown, handle = fmt(name) if name else ("", "")
    if shown:
        return shown, handle
    if parts:
        shown, handle = fmt(parts[0])
        return shown or "Untitled chat", handle
    return "Untitled chat", ""


def _enrich_chats(
    chats: list[dict[str, Any]],
    directory: dict[str, str] | None = None,
) -> None:
    owners = _infer_owners(chats)
    directory = directory or {}
    for c in chats:
        parts = [clean_party_label(p) for p in (c.get("participants") or [])]
        parts = [p for p in parts if p]
        c["participants"] = parts
        counterparties = [p for p in parts if p not in owners]
        c["counterparties"] = [
            _display_party(p, owners, directory) for p in counterparties
        ]
        title, handle = _chat_title(c, owners, directory)
        c["title"] = title
        c["handle"] = handle
        for m in c.get("messages") or []:
            if m.get("from"):
                m["from"] = _display_party(m["from"], owners, directory)


def _normalize_message(msg: Any, idx: int) -> dict[str, Any]:
    plain = _to_plain(msg) if not isinstance(msg, dict) else msg
    body = _pick(plain, "Body", "body", "Text", "text", "Subject", "subject", default="")
    ts = _pick(plain, "TimeStamp", "Timestamp", "timestamp", "Date", "date", "Time", "time")
    sender = _party_label(_pick(plain, "From", "from", "Sender", "sender"))
    status = _pick(plain, "Status", "status", "Folder", "folder", default="")
    source = _pick(plain, "Source", "source", "Application", "application", default="")
    mid = _pick(plain, "id", "Id", "ID", default=idx)
    return {
        "id": str(mid),
        "body": "" if body is None else str(body),
        "timestamp": "" if ts is None else str(ts),
        "from": sender,
        "status": "" if status is None else str(status),
        "source": "" if source is None else str(source),
    }


def _normalize_chat(chat: Any, idx: int) -> dict[str, Any]:
    plain = _to_plain(chat) if not isinstance(chat, dict) else chat
    name = _pick(plain, "Name", "name", "DisplayName", "displayName", "Id", "id", default=f"Chat {idx + 1}")
    source = _pick(plain, "Source", "source", "Application", "application", "Account", "account", default="")
    start = _pick(plain, "StartTime", "startTime", "FirstActivity", "firstActivity")
    last = _pick(plain, "LastActivity", "lastActivity", "EndTime", "endTime")
    cid = _pick(plain, "id", "Id", "ID", "Identifier", "identifier", default=idx)
    participants_raw = _pick(plain, "Participants", "participants", default=[]) or []
    if not isinstance(participants_raw, list):
        participants_raw = [participants_raw]
    participants = [_party_label(p) for p in participants_raw if _party_label(p)]
    messages_raw = _pick(plain, "Messages", "messages", default=[]) or []
    if not isinstance(messages_raw, list):
        messages_raw = [messages_raw]
    messages = [_normalize_message(m, i) for i, m in enumerate(messages_raw)]
    messages.sort(key=lambda m: m.get("timestamp") or "")
    preview = ""
    for m in reversed(messages):
        if m.get("body"):
            preview = str(m["body"])[:160]
            break
    return {
        "id": str(cid),
        "name": str(name),
        "source": "" if source is None else str(source),
        "participants": participants,
        "start": "" if start is None else str(start),
        "last_activity": "" if last is None else str(last),
        "message_count": len(messages),
        "preview": preview,
        "messages": messages,
    }


def parse_ufdr_chats_for_case(
    files_root: str | Path,
    rel_path: str,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    root = Path(files_root)
    ufdr_path = (root / rel_path).resolve()
    if not str(ufdr_path).startswith(str(root.resolve())):
        raise ValueError("path escapes case root")
    if not ufdr_path.is_file():
        raise FileNotFoundError(rel_path)
    if not ufedlib_available():
        raise RuntimeError(
            "ufedlib-py is not installed. Install with: uv add ufedlib-py && uv sync"
        )

    from ufedlib_py import parse_data

    cache_file = _cache_dir(root) / f"{_cache_key(ufdr_path)}.json"
    return _parse_and_normalize(
        ufdr_path,
        cache_file=cache_file,
        use_cache=use_cache,
        parse_data=parse_data,
        rel_path=rel_path,
    )


def _parse_and_normalize(
    ufdr_path: Path,
    *,
    cache_file: Path | None,
    use_cache: bool,
    parse_data: Any,
    rel_path: str | None = None,
) -> dict[str, Any]:
    if use_cache and cache_file and cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            chats = cached.get("chats") or []
            if chats and (not chats[0].get("title") or "handle" not in chats[0]):
                directory = build_contact_directory(ufdr_path)
                _enrich_chats(chats, directory)
                cached["owners"] = sorted(_infer_owners(chats))
                cached["directory_size"] = len(directory)
            return cached
        except Exception as e:
            log.warning("UFDR cache read failed %s: %s", cache_file, e)

    chats_raw = parse_data(str(ufdr_path), "Chat") or []
    if not isinstance(chats_raw, list):
        chats_raw = [chats_raw]

    chats = [_normalize_chat(c, i) for i, c in enumerate(chats_raw)]
    directory = build_contact_directory(ufdr_path)
    _enrich_chats(chats, directory)
    # Alphabetical apps; newest activity first within each app
    by_src: dict[str, list[dict[str, Any]]] = {}
    for c in chats:
        by_src.setdefault(str(c.get("source") or "Unknown"), []).append(c)
    ordered: list[dict[str, Any]] = []
    for src in sorted(by_src.keys(), key=str.lower):
        group = by_src[src]
        group.sort(
            key=lambda c: (
                str(c.get("last_activity") or ""),
                int(c.get("message_count") or 0),
            ),
            reverse=True,
        )
        ordered.extend(group)
    chats = ordered

    owners = sorted(_infer_owners(chats))
    payload = {
        "rel_path": rel_path or ufdr_path.name,
        "name": ufdr_path.name,
        "chat_count": len(chats),
        "message_count": sum(int(c.get("message_count") or 0) for c in chats),
        "sources": sorted({c.get("source") or "Unknown" for c in chats}),
        "owners": owners,
        "directory_size": len(directory),
        "chats": chats,
        "parser": "ufedlib-py",
        "note": (
            "Chats are taken from Cellebrite's decoded report.xml inside the UFDR — "
            "the same artifacts shown in Cellebrite Reader for this export. "
            "Contact names are resolved from decoded Contact/Party records when present; "
            "many SMS threads only have numbers if the export lacks Address Book names."
        ),
    }

    if cache_file is not None:
        try:
            cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except Exception as e:
            log.warning("UFDR cache write failed %s: %s", cache_file, e)

    return payload


def chat_summaries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Strip message bodies for list endpoints."""
    out = []
    for c in payload.get("chats") or []:
        out.append(
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "title": c.get("title") or c.get("name"),
                "handle": c.get("handle") or "",
                "source": c.get("source"),
                "participants": c.get("participants") or [],
                "counterparties": c.get("counterparties") or [],
                "start": c.get("start"),
                "last_activity": c.get("last_activity"),
                "message_count": c.get("message_count") or 0,
                "preview": c.get("preview") or "",
            }
        )
    return out


def get_chat(payload: dict[str, Any], chat_id: str) -> dict[str, Any] | None:
    for c in payload.get("chats") or []:
        if str(c.get("id")) == str(chat_id):
            return c
    # fallback by index
    try:
        idx = int(chat_id)
        chats = payload.get("chats") or []
        if 0 <= idx < len(chats):
            return chats[idx]
    except ValueError:
        pass
    return None


def find_csli_hints(files_root: str | Path, *, limit: int = 30) -> list[dict[str, Any]]:
    """Heuristic filename scan for CSLI-like materials (subsection visibility only)."""
    root = Path(files_root)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    scanned = 0
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            scanned += 1
            if scanned > 8000:
                break
            if not _CSLI_NAME.search(p.name):
                continue
            try:
                rel = str(p.relative_to(root)).replace("\\", "/")
                out.append({"rel_path": rel, "name": p.name, "size": p.stat().st_size})
            except OSError:
                continue
            if len(out) >= limit:
                break
    except OSError as e:
        log.warning("CSLI scan failed under %s: %s", root, e)
    return out


def case_forensics_summary(files_root: str | Path) -> dict[str, Any]:
    ufdrs = find_ufdr_files(files_root)
    csli = find_csli_hints(files_root)
    return {
        "ufedlib": ufedlib_available(),
        "available": bool(ufdrs or csli),
        "sections": {
            "cellebrite": {
                "available": bool(ufdrs),
                "label": "Cellebrite",
                "sources": ufdrs,
            },
            "csli": {
                "available": bool(csli),
                "label": "Cell Site Location Information",
                "sources": csli,
            },
        },
    }
