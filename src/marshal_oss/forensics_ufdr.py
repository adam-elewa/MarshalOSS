"""Cellebrite UFDR chat extraction via ufedlib-py (Reader-equivalent decoded chats)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

_CSLI_NAME = re.compile(
    r"(csli|cell[\s_-]?site|cellsites?|tower[\s_-]?dump|towers?[_\s-]?dumped|"
    r"first[_\s-]?serving|last[_\s-]?serving|serving[_\s-]?system|"
    r"\bcdr\b|cdr[_-]|neustar|call[\s_-]?detail|location[\s_-]?history|pen[_\s-]?register|"
    r"reportau|reportct|subscriber|sub_amdocs|sub_tibco|cell[\s_-]?cdw|"
    r"voice[\s_-]?usage|smscontent|90\s*days\s*voice)",
    re.I,
)
_CSLI_EXT = {".xls", ".xlsx", ".csv", ".tsv", ".txt"}
_ATT_MSISDN_RE = re.compile(r"MSISDN:\s*\(?(\d{3})\)?\s*[\-\s]?(\d{3})[\-\s]?(\d{4})", re.I)
_ATT_VOICE_FOR_RE = re.compile(
    r"Voice Usage For:\s*\(?(\d{3})\)?\s*[\-\s]?(\d{3})[\-\s]?(\d{4})", re.I
)
_ATT_NAME_RE = re.compile(r"^\s*Name:\s*(.+?)\s*$", re.I | re.M)
_ATT_USER_BLOCK_RE = re.compile(
    r"USER INFORMATION(?P<body>.+?)(?=STATUS CHANGE HISTORY|AT&T PROPRIETARY|\Z)",
    re.I | re.S,
)
_PHONE_IN_PATH_RE = re.compile(
    r"(?<!\d)(\d{3})[\-\s\.]?(\d{3})[\-\s\.]?(\d{4})(?!\d)"
)
_ATT_CELL_RE = re.compile(
    r"\[(?P<body>[^\]]+)\]"
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
    """Heuristic filename scan for CSLI / CDR / serving-system spreadsheets."""
    root = Path(files_root)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    scanned = 0
    try:
        # Prefer shallow/common forensic sample folders first
        candidates: list[Path] = []
        for prefer in ("forensics-samples", ".marshaloss/forensics"):
            d = root / prefer
            if d.is_dir():
                candidates.extend(d.rglob("*"))
        candidates.extend(root.rglob("*"))
        seen: set[str] = set()
        for p in candidates:
            if not p.is_file():
                continue
            scanned += 1
            if scanned > 20000:
                break
            try:
                rel = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
            if rel in seen:
                continue
            ext = p.suffix.lower()
            name_hit = bool(_CSLI_NAME.search(p.name) or _CSLI_NAME.search(rel))
            if ext not in _CSLI_EXT:
                continue
            if not name_hit:
                # Spreadsheet/txt without a CSLI/subscriber-ish name — skip
                continue
            seen.add(rel)
            try:
                out.append(
                    {
                        "rel_path": rel,
                        "name": p.name,
                        "size": p.stat().st_size,
                        "kind": ext.lstrip(".") or "file",
                    }
                )
            except OSError:
                continue
            if len(out) >= limit:
                break
    except OSError as e:
        log.warning("CSLI scan failed under %s: %s", root, e)
    return out


def _col_match(columns: list[str], *patterns: str) -> str | None:
    for col in columns:
        c = re.sub(r"\s+", " ", str(col).strip().lower())
        for pat in patterns:
            if re.search(pat, c, re.I):
                return str(col)
    return None


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        import math

        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        s = str(val).strip().replace(",", "")
        if not s or s.lower() in {"nan", "none", "null"}:
            return None
        try:
            f = float(s)
        except ValueError:
            return None
        return f


def _load_csli_dataframe(path: Path):
    import pandas as pd

    ext = path.suffix.lower()
    if ext in {".csv", ".tsv"}:
        return pd.read_csv(path, dtype=str, low_memory=False)
    if ext == ".xls":
        return pd.read_excel(path, engine="xlrd")
    if ext == ".xlsx":
        return pd.read_excel(path, engine="openpyxl")
    raise ValueError(f"unsupported CSLI type: {ext}")


def _display_phone(digits: str) -> str:
    d = re.sub(r"\D", "", str(digits or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) == 10:
        return d
    return d or str(digits or "").strip()


def _phones_from_path(text: str) -> list[str]:
    out: list[str] = []
    for m in _PHONE_IN_PATH_RE.finditer(str(text or "")):
        out.append(_display_phone("".join(m.groups())))
    return out


def _is_subscriber_structure_file(path: Path) -> bool:
    n = path.name.lower()
    return bool(
        re.search(r"reportct|subscriberinformation|subscriber__|sub_amdocs|sub_tibco|^sub_", n)
        or ("subscriber" in n and path.suffix.lower() in {".xls", ".xlsx", ".csv", ".txt"})
    )


def _is_att_usage_file(path: Path) -> bool:
    return bool(re.search(r"reportau", path.name, re.I)) and path.suffix.lower() in {
        ".txt",
        ".csv",
    }


def parse_att_report_ct(path: Path) -> list[dict[str, Any]]:
    """AT&T Wireless SUBSCRIBER INFORMATION (ReportCT .txt)."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    out: list[dict[str, Any]] = []
    for block in _ATT_USER_BLOCK_RE.finditer(text):
        body = block.group("body")
        msisdn_m = _ATT_MSISDN_RE.search(body)
        if not msisdn_m:
            continue
        phone = _display_phone("".join(msisdn_m.groups()))
        name = ""
        for nm in _ATT_NAME_RE.finditer(body):
            cand = nm.group(1).strip()
            if cand and cand.upper() not in {"N/A", "NONE", "NULL"}:
                name = cand
                break
        addr_m = re.search(r"User Address:\s*(.+?)\s*$", body, re.I | re.M)
        out.append(
            {
                "phone": phone,
                "name": name,
                "address": (addr_m.group(1).strip() if addr_m else ""),
                "carrier": "AT&T",
                "source": path.name,
                "kind": "att_report_ct",
                "is_target": True,
            }
        )
    # Fallback: Voice Usage For phone + first Name: in file
    if not out:
        vf = _ATT_VOICE_FOR_RE.search(text) or _ATT_MSISDN_RE.search(text)
        if vf:
            phone = _display_phone("".join(vf.groups()))
            nm = _ATT_NAME_RE.search(text)
            out.append(
                {
                    "phone": phone,
                    "name": (nm.group(1).strip() if nm else ""),
                    "address": "",
                    "carrier": "AT&T",
                    "source": path.name,
                    "kind": "att_report_ct",
                    "is_target": True,
                }
            )
    return out


def parse_att_report_au(path: Path, *, rel_path: str = "") -> list[dict[str, Any]]:
    """AT&T ReportAU voice/SMS usage with CellLocation brackets → map events."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    phone = ""
    vf = _ATT_VOICE_FOR_RE.search(text)
    if vf:
        phone = _display_phone("".join(vf.groups()))
    if not phone:
        for p in _phones_from_path(path.parent.name + " " + path.name):
            phone = p
            break
    events: list[dict[str, Any]] = []
    # Data rows look like: 1,07/15/20 06:44:59,...,APPLE,IPHONE11,[cell...]
    for line in text.splitlines():
        line = line.strip()
        if not line or not re.match(r"^\d+,", line):
            continue
        cell_m = _ATT_CELL_RE.search(line)
        if not cell_m:
            continue
        parts = cell_m.group("body").split(":")
        # AT&T: ECGI:ENB-ID:LONG:LAT:Sector(azimuth):Beamwidth  — coords are the tower
        if len(parts) < 4:
            continue
        try:
            lon = float(parts[2])
            lat = float(parts[3])
        except ValueError:
            continue
        if lat == 0 and lon == 0:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        azimuth = _to_float(parts[4]) if len(parts) > 4 else None
        beamwidth = _to_float(parts[5]) if len(parts) > 5 else None
        if beamwidth is not None and beamwidth < 0:
            beamwidth = None  # -1 = not passed from network
        # ConnDateTime is 2nd CSV field
        fields = line.split(",")
        when_raw = fields[1].strip() if len(fields) > 1 else ""
        ts = None
        when = when_raw
        for fmt in ("%m/%d/%y %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
            try:
                dt = datetime.strptime(when_raw, fmt)
                ts = dt.strftime("%Y-%m-%dT%H:%M:%S")
                when = dt.strftime("%Y-%m-%d %H:%M:%S")
                break
            except ValueError:
                continue
        device = ""
        if len(fields) >= 16:
            make = fields[14].strip()
            model = fields[15].strip()
            if make or model:
                device = f"{make} | {model}".strip(" |")
        events.append(
            {
                "lat": lat,
                "lon": lon,
                "phone": phone,
                "when": when,
                "ts": ts,
                "cell": parts[0],
                "enb": parts[1] if len(parts) > 1 else "",
                "sector": str(int(azimuth)) if azimuth is not None else "",
                "azimuth": azimuth,
                "beamwidth": beamwidth,
                "device": device,
                "carrier": "AT&T",
                "name": "",
                "label": phone or "AT&T event",
                "source": path.name,
                "rel_path": rel_path or path.name,
                # AT&T ReportAU ConnDateTime is UTC (see ATT_Records_Key).
                "source_tz": "UTC",
            }
        )
    return events


def parse_tmobile_subscriber_xls(path: Path) -> list[dict[str, Any]]:
    """T-Mobile SUB_*.xls sheets named like '1. Subscriber Report'."""
    import xlrd

    book = xlrd.open_workbook(str(path))
    out: list[dict[str, Any]] = []
    for sh in book.sheets():
        if not re.search(r"subscriber\s*report", sh.name, re.I):
            continue
        labels: dict[str, str] = {}
        for r in range(sh.nrows):
            # label often in col 1, value in col 3
            label = str(sh.cell_value(r, 1) if sh.ncols > 1 else "").strip()
            val = ""
            for c in range(2, min(sh.ncols, 5)):
                raw = sh.cell_value(r, c)
                if raw in ("", None):
                    continue
                if isinstance(raw, float) and raw.is_integer():
                    val = str(int(raw))
                else:
                    val = str(raw).strip()
                if val:
                    break
            if label and val and val.lower() != "no records found":
                labels[label.lower()] = val
        phone = _display_phone(
            labels.get("msisdn no")
            or labels.get("msisdn")
            or labels.get("device number")
            or ""
        )
        if not phone:
            # warrant blurb often embeds MSISDN
            for r in range(min(12, sh.nrows)):
                blob = " ".join(str(sh.cell_value(r, c)) for c in range(sh.ncols))
                m = re.search(r"MSISDN:\s*(\d{10,15})", blob, re.I)
                if m:
                    phone = _display_phone(m.group(1))
                    break
        name = (
            labels.get("subscriber name")
            or labels.get("msisdn name")
            or labels.get("account name")
            or labels.get("bill name")
            or ""
        )
        if phone or name:
            out.append(
                {
                    "phone": phone,
                    "name": name,
                    "address": labels.get("subscriber address") or labels.get("bill address") or "",
                    "carrier": "T-Mobile",
                    "source": path.name,
                    "kind": "tmobile_subscriber_xls",
                    "is_target": True,
                }
            )
    return out


def parse_verizon_subscriber_csv(path: Path) -> list[dict[str, Any]]:
    """Verizon SubscriberInformation / Subscriber__MTN-*.csv."""
    import pandas as pd

    df = pd.read_csv(path, dtype=str, low_memory=False)
    cols = [str(c) for c in df.columns]
    phone_col = _col_match(cols, r"^mtn$", r"search\s*value", r"mobile", r"phone")
    first = _col_match(cols, r"^first\s*name$")
    last = _col_match(cols, r"^last\s*name$")
    biz = _col_match(cols, r"business\s*name")
    out: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        phone = _display_phone(row.get(phone_col) if phone_col else "")
        first_n = str(row.get(first) or "").strip() if first else ""
        last_n = str(row.get(last) or "").strip() if last else ""
        biz_n = str(row.get(biz) or "").strip() if biz else ""
        name = " ".join(x for x in (first_n, last_n) if x and x.upper() != "NAME").strip()
        if not name and biz_n and biz_n.upper() not in {"NAME", "NO"}:
            name = biz_n
        if not phone:
            continue
        out.append(
            {
                "phone": phone,
                "name": name,
                "address": "",
                "carrier": "Verizon",
                "source": path.name,
                "kind": "verizon_subscriber_csv",
                "is_target": True,
            }
        )
    return out


def extract_subscribers_from_file(path: Path) -> list[dict[str, Any]]:
    """Deterministic subscriber parsers for carrier return structures."""
    n = path.name.lower()
    ext = path.suffix.lower()
    try:
        if re.search(r"reportct", n) and ext in {".txt", ".csv"}:
            return parse_att_report_ct(path)
        if re.search(r"subscriberinformation|subscriber__", n) and ext == ".csv":
            return parse_verizon_subscriber_csv(path)
        if re.search(r"sub_amdocs|sub_tibco|^sub_", n) and ext in {".xls", ".xlsx"}:
            return parse_tmobile_subscriber_xls(path)
        if ext in {".xls", ".xlsx"}:
            # Any workbook with a Subscriber Report sheet
            try:
                return parse_tmobile_subscriber_xls(path)
            except Exception:
                return []
    except Exception as e:
        log.warning("Subscriber parse failed for %s: %s", path, e)
    return []


def infer_target_phones_from_paths(sources: list[dict[str, Any]]) -> list[str]:
    """Phones embedded in CDW/folder/file names (e.g. '…732-997-2985')."""
    found: list[str] = []
    seen: set[str] = set()
    for s in sources:
        blob = f"{s.get('rel_path') or ''} {s.get('name') or ''}"
        for phone in _phones_from_path(blob):
            if phone not in seen:
                seen.add(phone)
                found.append(phone)
    return found


def _combine_csli_ts(date_val: Any, time_val: Any) -> tuple[str | None, str]:
    """Combine Date + Access Time into (iso_ts, display)."""
    from datetime import date, datetime, time

    d: date | None = None
    t: time | None = None

    if isinstance(date_val, datetime):
        d = date_val.date()
        if date_val.time() != time(0, 0) and time_val is None:
            t = date_val.time()
    elif isinstance(date_val, date):
        d = date_val
    elif date_val is not None and str(date_val).strip():
        s = str(date_val).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                dt = datetime.strptime(s[:19], fmt)
                d = dt.date()
                if dt.time() != time(0, 0) and time_val is None:
                    t = dt.time()
                break
            except ValueError:
                continue

    if isinstance(time_val, datetime):
        t = time_val.time()
    elif isinstance(time_val, time):
        t = time_val
    elif time_val is not None and str(time_val).strip():
        s = str(time_val).strip()
        for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
            try:
                t = datetime.strptime(s, fmt).time()
                break
            except ValueError:
                continue

    if d and t:
        dt = datetime.combine(d, t)
        return dt.strftime("%Y-%m-%dT%H:%M:%S"), dt.strftime("%Y-%m-%d %H:%M:%S")
    if d:
        return d.isoformat() + "T00:00:00", d.isoformat()
    if t:
        return None, t.strftime("%H:%M:%S")
    return None, ""


def _extract_csli_geo(df, *, source_name: str, rel_path: str) -> dict[str, Any]:
    """Pull tower sites and call/event pings with coordinates from a dataframe."""
    cols = [str(c) for c in df.columns]
    lat_col = _col_match(
        cols,
        r"e-?911\s*latitude",
        r"call\s*latitude",
        r"^latitude",
        r"\blat\b",
        r"begin\s*lat",
        r"end\s*lat",
    )
    lon_col = _col_match(
        cols,
        r"e-?911\s*longitude",
        r"call\s*longitude",
        r"^longitude",
        r"\blon\b",
        r"\blng\b",
        r"begin\s*lon",
        r"end\s*lon",
    )
    if not lat_col or not lon_col:
        return {
            "rel_path": rel_path,
            "name": source_name,
            "towers": [],
            "events": [],
            "skipped_reason": "no latitude/longitude columns",
        }

    site_col = _col_match(cols, r"site\s*number", r"site\s*#", r"cell\s*site", r"^site$")
    addr_col = _col_match(cols, r"street\s*address", r"^address$")
    city_col = _col_match(cols, r"^city$")
    sector_col = _col_match(cols, r"^sector$", r"init\s*sector", r"cell\s*face")
    az_col = _col_match(cols, r"azimuth")
    hbw_col = _col_match(cols, r"h-?bw", r"beam\s*width", r"antenna\s*h")
    phone_col = _col_match(
        cols,
        r"subscriber\s*#",
        r"^subscriber$",
        r"mobile\s*directory",
        r"\bmdn\b",
        r"target\s*number",
        r"calling\s*party",
    )
    device_col = _col_match(cols, r"mfg\s*\|\s*model", r"device\s*model", r"^model$")
    carrier_col = _col_match(cols, r"carrier\s*name", r"^carrier$")
    # Prefer real clock columns — never match a Date column as "time"
    time_col = _col_match(
        cols,
        r"^access\s*time$",
        r"access\s*time",
        r"seizure\s*dt",
        r"start\s*time",
        r"established\s*time",
        r"call\s*end\s*time",
    )
    date_col = _col_match(cols, r"^date\s*\*?\s*$", r"^date\b", r"start\s*date")
    cell_col = _col_match(cols, r"init\s*cell", r"first\s*serving\s*cell", r"begin\s*cell", r"^cell$")
    kind_hint = "tower" if site_col and az_col and not phone_col else "event"
    # Tower lists usually have Site Number + Azimuth; CDR pings have Call Latitude + Subscriber
    if _col_match(cols, r"e-?911\s*latitude") and site_col:
        kind_hint = "tower"
    if _col_match(cols, r"call\s*latitude") or phone_col:
        kind_hint = "event" if not (site_col and az_col and _col_match(cols, r"e-?911")) else kind_hint

    towers: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    tower_keys: set[str] = set()

    for _, row in df.iterrows():
        lat = _to_float(row.get(lat_col))
        lon = _to_float(row.get(lon_col))
        if lat is None or lon is None:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        # NJ/US sanity: skip clearly wrong zeros
        if lat == 0 and lon == 0:
            continue

        site = str(row.get(site_col) or "").strip() if site_col else ""
        sector = str(row.get(sector_col) or "").strip() if sector_col else ""
        az = _to_float(row.get(az_col)) if az_col else None
        hbw = _to_float(row.get(hbw_col)) if hbw_col else None
        addr_parts = []
        if addr_col and str(row.get(addr_col) or "").strip():
            addr_parts.append(str(row.get(addr_col)).strip())
        if city_col and str(row.get(city_col) or "").strip():
            addr_parts.append(str(row.get(city_col)).strip())
        address = ", ".join(addr_parts)
        phone = str(row.get(phone_col) or "").strip() if phone_col else ""
        if phone in {"0", "nan", "None"}:
            phone = ""
        device = ""
        if device_col and str(row.get(device_col) or "").strip() not in {"", "0", "nan", "None"}:
            device = str(row.get(device_col)).strip()
        carrier = ""
        if carrier_col and str(row.get(carrier_col) or "").strip() not in {"", "0", "nan", "None"}:
            carrier = str(row.get(carrier_col)).strip()
        ts, when = _combine_csli_ts(
            row.get(date_col) if date_col else None,
            row.get(time_col) if time_col else None,
        )
        cell = str(row.get(cell_col) or "").strip() if cell_col else ""

        is_tower = kind_hint == "tower" or (site and az is not None and not phone)
        if is_tower:
            key = f"{round(lat, 5)}|{round(lon, 5)}|{site}|{sector}|{az}"
            if key in tower_keys:
                continue
            tower_keys.add(key)
            towers.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "site": site,
                    "sector": sector,
                    "azimuth": az,
                    "beamwidth": hbw if hbw is not None else 65.0,
                    "address": address,
                    "label": (f"Site {site}" if site else "Tower")
                    + (f" · sector {sector}" if sector else "")
                    + (f" · {int(az)}°" if az is not None else ""),
                    "source": source_name,
                    "rel_path": rel_path,
                }
            )
        else:
            events.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "phone": phone,
                    "when": when,
                    "ts": ts,
                    "cell": cell or site,
                    "sector": sector,
                    "device": device,
                    "carrier": carrier,
                    "name": "",
                    "label": phone or cell or "Event",
                    "source": source_name,
                    "rel_path": rel_path,
                    # Carrier tower/CDR sheets for NJ cases are typically local Eastern.
                    "source_tz": "America/New_York",
                }
            )

    return {
        "rel_path": rel_path,
        "name": source_name,
        "towers": towers,
        "events": events,
        "tower_count": len(towers),
        "event_count": len(events),
    }


def parse_csli_map_for_case(
    files_root: str | Path,
    *,
    rel_paths: list[str] | None = None,
    use_cache: bool = True,
    max_events: int = 5000,
) -> dict[str, Any]:
    """Build a map payload (towers + events) from CSLI/CDR geo spreadsheets."""
    root = Path(files_root).resolve()
    sources = find_csli_hints(root)
    if rel_paths:
        wanted = set(rel_paths)
        sources = [s for s in sources if s["rel_path"] in wanted]
        # allow explicit paths even if heuristic missed them
        for rp in rel_paths:
            if rp not in {s["rel_path"] for s in sources}:
                p = root / rp
                if p.is_file():
                    sources.append(
                        {
                            "rel_path": rp,
                            "name": p.name,
                            "size": p.stat().st_size,
                            "kind": p.suffix.lstrip("."),
                        }
                    )

    cache_dir = Path(files_root) / ".marshaloss" / "forensics" / "csli"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key_parts = []
    for s in sources:
        p = root / s["rel_path"]
        try:
            st = p.stat()
            cache_key_parts.append(f"{s['rel_path']}|{st.st_size}|{int(st.st_mtime)}")
        except OSError:
            cache_key_parts.append(s["rel_path"])
    raw = "v8|" + "|".join(cache_key_parts)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    cache_file = cache_dir / f"map-{digest}.json"
    if use_cache and cache_file.is_file():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("CSLI map cache read failed: %s", e)

    towers_raw: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    subscribers: list[dict[str, Any]] = []
    parsed_sources: list[dict[str, Any]] = []
    for s in sources:
        rel = s["rel_path"]
        path = (root / rel).resolve()
        if not str(path).startswith(str(root)) or not path.is_file():
            continue
        # Deterministic subscriber-structure parsers (AT&T ReportCT, T-Mobile SUB_, Verizon CSV)
        if _is_subscriber_structure_file(path):
            try:
                subs = extract_subscribers_from_file(path)
                subscribers.extend(subs)
                parsed_sources.append(
                    {
                        "rel_path": rel,
                        "name": path.name,
                        "tower_count": 0,
                        "event_count": 0,
                        "subscriber_count": len(subs),
                        "kind": "subscriber",
                    }
                )
            except Exception as e:
                parsed_sources.append(
                    {
                        "rel_path": rel,
                        "name": path.name,
                        "tower_count": 0,
                        "event_count": 0,
                        "error": str(e)[:300],
                    }
                )
            continue
        # AT&T ReportAU voice/SMS with CellLocation
        if _is_att_usage_file(path):
            try:
                au_events = parse_att_report_au(path, rel_path=rel)
                events.extend(au_events)
                parsed_sources.append(
                    {
                        "rel_path": rel,
                        "name": path.name,
                        "tower_count": 0,
                        "event_count": len(au_events),
                        "kind": "att_report_au",
                    }
                )
            except Exception as e:
                parsed_sources.append(
                    {
                        "rel_path": rel,
                        "name": path.name,
                        "tower_count": 0,
                        "event_count": 0,
                        "error": str(e)[:300],
                    }
                )
            continue
        if path.suffix.lower() == ".txt":
            continue
        try:
            df = _load_csli_dataframe(path)
            geo = _extract_csli_geo(df, source_name=path.name, rel_path=rel)
            towers_raw.extend(geo.get("towers") or [])
            events.extend(geo.get("events") or [])
            parsed_sources.append(
                {
                    "rel_path": rel,
                    "name": path.name,
                    "tower_count": geo.get("tower_count") or 0,
                    "event_count": geo.get("event_count") or 0,
                    "skipped_reason": geo.get("skipped_reason"),
                }
            )
        except Exception as e:
            parsed_sources.append(
                {
                    "rel_path": rel,
                    "name": path.name,
                    "tower_count": 0,
                    "event_count": 0,
                    "error": str(e)[:300],
                }
            )

    # Keep timed events for timeline filtering (mainstream CSLI UX).
    event_total = len(events)
    events.sort(key=lambda e: (e.get("ts") or e.get("when") or "", e.get("phone") or ""))
    if len(events) > max_events:
        step = max(1, len(events) // max_events)
        events = events[::step][:max_events]

    # Phone → name only from carrier subscriber structures (not UFDR contacts)
    sub_by_phone: dict[str, dict[str, Any]] = {}
    for sub in subscribers:
        phone = _display_phone(sub.get("phone") or "")
        if not phone:
            continue
        cur = sub_by_phone.get(phone)
        if not cur or (sub.get("name") and not cur.get("name")):
            sub_by_phone[phone] = dict(sub)
            sub_by_phone[phone]["phone"] = phone

    target_phones = set(infer_target_phones_from_paths(sources))
    for sub in subscribers:
        if sub.get("is_target") and sub.get("phone"):
            target_phones.add(_display_phone(sub["phone"]))

    phone_meta: dict[str, dict[str, Any]] = {}
    for i, e in enumerate(events):
        e["id"] = f"e{i}"
        e["count"] = 1
        phone = _display_phone(e.get("phone") or "")
        e["phone"] = phone
        sub = sub_by_phone.get(phone) if phone else None
        name = (sub or {}).get("name") or ""
        if name and not _is_phone_label(name):
            e["name"] = name
            e["label"] = name
        else:
            e["name"] = e.get("name") or ""
        if phone:
            meta = phone_meta.get(phone)
            if not meta:
                phone_meta[phone] = {
                    "phone": phone,
                    "name": e.get("name") or "",
                    "device": e.get("device") or "",
                    "carrier": e.get("carrier") or (sub or {}).get("carrier") or "",
                    "address": (sub or {}).get("address") or "",
                    "count": 1,
                    "is_target": phone in target_phones,
                }
            else:
                meta["count"] += 1
                if not meta.get("name") and e.get("name"):
                    meta["name"] = e["name"]
                if not meta.get("device") and e.get("device"):
                    meta["device"] = e["device"]
                if not meta.get("carrier") and (e.get("carrier") or (sub or {}).get("carrier")):
                    meta["carrier"] = e.get("carrier") or (sub or {}).get("carrier")
                if phone in target_phones:
                    meta["is_target"] = True

    # Include subscriber-only phones (named but maybe no geo yet)
    for phone, sub in sub_by_phone.items():
        if phone not in phone_meta:
            phone_meta[phone] = {
                "phone": phone,
                "name": sub.get("name") or "",
                "device": "",
                "carrier": sub.get("carrier") or "",
                "address": sub.get("address") or "",
                "count": 0,
                "is_target": phone in target_phones or bool(sub.get("is_target")),
            }

    phones = sorted(
        phone_meta.values(),
        key=lambda p: (
            0 if p.get("is_target") else 1,
            0 if p.get("name") else 1,
            -int(p.get("count") or 0),
            str(p.get("name") or p.get("phone") or ""),
        ),
    )
    named_count = sum(1 for p in phones if p.get("name"))
    primary_phone = next((p["phone"] for p in phones if p.get("is_target") and p.get("count")), None)
    if not primary_phone:
        primary_phone = next((p["phone"] for p in phones if p.get("is_target")), None)
    if not primary_phone and phones:
        primary_phone = phones[0]["phone"]

    days = sorted(
        {
            str(e["ts"])[:10]
            for e in events
            if e.get("ts") and len(str(e["ts"])) >= 10
        }
    )
    time_min = next((e.get("ts") for e in events if e.get("ts")), None)
    time_max = next((e.get("ts") for e in reversed(events) if e.get("ts")), None)

    # Focus on the activity area (pings), not the entire statewide tower catalog
    if events:
        elats = [e["lat"] for e in events]
        elons = [e["lon"] for e in events]
        pad = 0.04  # ~4km
        min_lat, max_lat = min(elats) - pad, max(elats) + pad
        min_lon, max_lon = min(elons) - pad, max(elons) + pad
        towers_raw = [
            t
            for t in towers_raw
            if min_lat <= t["lat"] <= max_lat and min_lon <= t["lon"] <= max_lon
        ]
        bounds = [[min(elats) - pad * 0.4, min(elons) - pad * 0.4], [max(elats) + pad * 0.4, max(elons) + pad * 0.4]]
        center = {"lat": sum(elats) / len(elats), "lon": sum(elons) / len(elons)}
    else:
        lats = [t["lat"] for t in towers_raw]
        lons = [t["lon"] for t in towers_raw]
        bounds = None
        center = {"lat": 40.49, "lon": -74.45}
        if lats and lons:
            bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]
            center = {"lat": sum(lats) / len(lats), "lon": sum(lons) / len(lons)}

    # One marker per site location (collapse sector duplicates)
    by_site: dict[str, dict[str, Any]] = {}
    for t in towers_raw:
        key = f"{round(t['lat'], 5)}|{round(t['lon'], 5)}|{t.get('site') or ''}"
        cur = by_site.get(key)
        if not cur:
            by_site[key] = {
                "lat": t["lat"],
                "lon": t["lon"],
                "site": t.get("site") or "",
                "address": t.get("address") or "",
                "label": f"Site {t.get('site')}" if t.get("site") else (t.get("label") or "Tower"),
                "source": t.get("source") or "",
                "rel_path": t.get("rel_path") or "",
                "sectors": [],
            }
            cur = by_site[key]
        if t.get("azimuth") is not None:
            cur["sectors"].append(
                {
                    "sector": t.get("sector") or "",
                    "azimuth": t.get("azimuth"),
                    "beamwidth": t.get("beamwidth") if t.get("beamwidth") is not None else 65.0,
                }
            )
    towers = list(by_site.values())

    payload = {
        "tower_count": len(towers),
        "event_count": len(events),
        "event_raw_count": event_total,
        "towers": towers,
        "events": events,
        "phones": phones,
        "named_phone_count": named_count,
        "primary_phone": primary_phone,
        "subscribers": list(sub_by_phone.values()),
        "bounds": bounds,
        "center": center,
        "days": days,
        "time_min": time_min,
        "time_max": time_max,
        "sources": parsed_sources,
        "note": "Names from carrier subscriber returns only (AT&T ReportCT, T-Mobile Subscriber Report, Verizon Subscriber CSV).",
    }
    try:
        cache_file.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        log.warning("CSLI map cache write failed: %s", e)
    return payload


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


def query_csli_events(
    files_root: str | Path,
    *,
    phone: str | None = None,
    day: str | None = None,
    start: str | None = None,
    end: str | None = None,
    source_path: str | None = None,
    limit: int = 200,
    offset: int = 0,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Return filtered CSLI events for arbitrary host-side analysis (MCP/Cowork).

    Times are as stored on each record (`source_tz` when known; AT&T ReportAU is UTC).
    No scene-specific logic — callers compute proximity, time windows, etc.
    """
    payload = parse_csli_map_for_case(files_root, use_cache=use_cache)
    events = list(payload.get("events") or [])

    phone_digits = "".join(ch for ch in str(phone or "") if ch.isdigit()) or None
    day_s = (day or "").strip()[:10] or None
    start_s = (start or "").strip().replace("T", " ") or None
    end_s = (end or "").strip().replace("T", " ") or None
    src_sub = (source_path or "").strip().lower() or None

    def when_key(e: dict[str, Any]) -> str:
        return str(e.get("ts") or e.get("when") or "").replace("T", " ")

    filtered: list[dict[str, Any]] = []
    for e in events:
        if phone_digits:
            pd = "".join(ch for ch in str(e.get("phone") or e.get("label") or "") if ch.isdigit())
            if pd != phone_digits and not pd.endswith(phone_digits) and not phone_digits.endswith(pd):
                continue
        wk = when_key(e)
        if day_s and not wk.startswith(day_s):
            continue
        if start_s and wk[: len(start_s)] < start_s:
            continue
        if end_s and wk[: len(end_s)] > end_s:
            continue
        if src_sub:
            blob = f"{e.get('rel_path') or ''} {e.get('source') or ''}".lower()
            if src_sub not in blob:
                continue
        filtered.append(e)

    filtered.sort(key=when_key)
    total = len(filtered)
    limit = max(1, min(int(limit or 200), 2000))
    offset = max(0, int(offset or 0))
    page = filtered[offset : offset + limit]

    slim = []
    for e in page:
        slim.append(
            {
                "when": e.get("when") or e.get("ts"),
                "ts": e.get("ts") or e.get("when"),
                "source_tz": e.get("source_tz"),
                "lat": e.get("lat"),
                "lon": e.get("lon"),
                "azimuth": e.get("azimuth"),
                "beamwidth": e.get("beamwidth"),
                "phone": e.get("phone") or e.get("label"),
                "name": e.get("name") or "",
                "carrier": e.get("carrier") or "",
                "cell": e.get("cell"),
                "enb": e.get("enb"),
                "sector": e.get("sector"),
                "device": e.get("device") or "",
                "rel_path": e.get("rel_path") or e.get("source"),
                "source": e.get("source"),
            }
        )

    phones = sorted(
        {
            "".join(ch for ch in str(e.get("phone") or "") if ch.isdigit())
            for e in (payload.get("events") or [])
            if e.get("phone")
        }
    )
    phones = [p for p in phones if p]

    return {
        "event_count": total,
        "returned": len(slim),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(slim) < total,
        "next_offset": (offset + len(slim)) if offset + len(slim) < total else None,
        "filters": {
            "phone": phone_digits,
            "day": day_s,
            "start": start_s,
            "end": end_s,
            "source_path": source_path or None,
        },
        "phones_in_case": phones,
        "events": slim,
        "note": (
            "Times are as recorded on each row; see source_tz (AT&T ReportAU is UTC). "
            "Coordinates are the serving cell/tower site, not handset GPS. "
            "Analyze freely — convert timezones, measure distance to any point, etc."
        ),
    }


def search_forensics(
    files_root: str | Path,
    query: str,
    *,
    top_k: int = 25,
    map_pins: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Search forensic sources + CSLI events (+ optional map pins) for MCP/UI.

    Matches phone digits, path/name tokens, dates (YYYY-MM-DD), and free text
    against carrier returns, UFDR packages, and geolocated CSLI records.
    """
    q = (query or "").strip()
    if not q:
        return {"query": q, "hits": [], "note": "empty query"}
    q_lower = q.lower()
    q_digits = "".join(ch for ch in q if ch.isdigit())
    tokens = [t for t in re.split(r"\s+", q_lower) if t]
    top_k = max(1, min(int(top_k or 25), 100))

    hits: list[dict[str, Any]] = []

    def score_text(*parts: Any) -> int:
        blob = " ".join(str(p or "") for p in parts).lower()
        if not blob:
            return 0
        score = 0
        if q_lower in blob:
            score += 40
        for t in tokens:
            if t in blob:
                score += 8
        if q_digits and len(q_digits) >= 7:
            blob_digits = "".join(ch for ch in blob if ch.isdigit())
            if q_digits in blob_digits:
                score += 50
            elif q_digits[-10:] and q_digits[-10:] in blob_digits:
                score += 35
        return score

    # Forensic files (CSLI spreadsheets + UFDR packages)
    for src in find_csli_hints(files_root, limit=80):
        sc = score_text(src.get("rel_path"), src.get("name"), src.get("kind"), "csli")
        if sc <= 0:
            continue
        hits.append(
            {
                "type": "forensic_file",
                "kind": "csli",
                "score": sc,
                "label": src.get("name") or src.get("rel_path"),
                "rel_path": src.get("rel_path"),
                "size": src.get("size"),
                "pin_ready": False,
            }
        )
    for src in find_ufdr_files(files_root, limit=50):
        sc = score_text(src.get("rel_path"), src.get("name"), "ufdr", "cellebrite")
        if sc <= 0:
            continue
        hits.append(
            {
                "type": "forensic_file",
                "kind": "cellebrite",
                "score": sc,
                "label": src.get("name") or src.get("rel_path"),
                "rel_path": src.get("rel_path"),
                "size": src.get("size"),
                "pin_ready": False,
            }
        )

    # Existing map pins
    for pin in map_pins or []:
        sc = score_text(
            pin.get("label"),
            pin.get("note"),
            pin.get("phone"),
            pin.get("source_path"),
            "pin",
            "map",
        )
        if sc <= 0:
            continue
        hits.append(
            {
                "type": "map_pin",
                "kind": pin.get("kind") or "user",
                "score": sc + 5,
                "id": pin.get("id"),
                "label": pin.get("label"),
                "lat": pin.get("lat"),
                "lon": pin.get("lon"),
                "note": pin.get("note") or "",
                "phone": pin.get("phone"),
                "source_path": pin.get("source_path"),
                "pin_ready": True,
            }
        )

    # CSLI geolocated events (cached parse)
    try:
        payload = parse_csli_map_for_case(files_root, use_cache=True)
    except Exception as e:
        payload = {"events": [], "sources": [], "error": str(e)[:200]}

    # Cap event scan for responsiveness
    events = payload.get("events") or []
    if len(events) > 8000:
        events = events[:8000]

    event_hits: list[dict[str, Any]] = []
    for e in events:
        sc = score_text(
            e.get("phone"),
            e.get("label"),
            e.get("name"),
            e.get("when"),
            e.get("ts"),
            e.get("cell"),
            e.get("source"),
            e.get("rel_path"),
            e.get("carrier"),
            e.get("device"),
        )
        if sc <= 0:
            continue
        lat, lon = e.get("lat"), e.get("lon")
        try:
            lat_f, lon_f = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        event_hits.append(
            {
                "type": "csli_event",
                "kind": "csli",
                "score": sc,
                "label": f"{e.get('phone') or e.get('label') or 'phone'} @ {e.get('when') or e.get('ts') or ''}".strip(),
                "phone": e.get("phone") or e.get("label"),
                "when": e.get("when") or e.get("ts"),
                "lat": lat_f,
                "lon": lon_f,
                "azimuth": e.get("azimuth"),
                "cell": e.get("cell"),
                "rel_path": e.get("rel_path") or e.get("source"),
                "carrier": e.get("carrier"),
                "pin_ready": True,
            }
        )
    # Deduplicate near-identical tower/time hits; keep best score
    event_hits.sort(key=lambda h: (-h["score"], str(h.get("when") or "")))
    seen_ev: set[tuple[Any, ...]] = set()
    for h in event_hits:
        key = (
            str(h.get("phone") or ""),
            str(h.get("when") or "")[:16],
            round(float(h["lat"]), 4),
            round(float(h["lon"]), 4),
        )
        if key in seen_ev:
            continue
        seen_ev.add(key)
        hits.append(h)

    hits.sort(key=lambda h: (-int(h.get("score") or 0), str(h.get("type")), str(h.get("label"))))
    truncated = hits[top_k:]
    hits = hits[:top_k]
    return {
        "query": q,
        "hit_count": len(hits),
        "truncated": bool(truncated),
        "hits": hits,
        "csli_event_total": len(payload.get("events") or []),
        "csli_source_count": len(payload.get("sources") or payload.get("parsed_sources") or []),
        "instructions": (
            "pin_ready hits can be dropped on the CSLI map via map_pin_add "
            "(lat/lon/label/note/phone/source_path). Forensic file hits are for "
            "browse/open; they have no coordinates unless paired with a csli_event."
        ),
    }


def ingest_ufdr_forensics(
    files_root: str | Path,
    *,
    force: bool = False,
    limit: int = 50,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Discover UFDRs under a case root and warm the Forensics chat cache.

    Runs during ``marshaloss ingest`` so Cellebrite chats + contact directory
    resolution are ready before anyone opens the Forensics tab. Safe to re-run:
    unchanged packages reuse the on-disk cache unless ``force`` is set.
    """
    root = Path(files_root).resolve()
    log_fn = progress or (lambda _m: None)

    summary: dict[str, Any] = {
        "ufdr_found": 0,
        "ufdr_parsed": 0,
        "ufdr_cached": 0,
        "ufdr_errors": 0,
        "chat_count": 0,
        "message_count": 0,
        "directory_size": 0,
        "sources": [],
        "errors": [],
    }

    if not root.is_dir():
        return summary

    if not ufedlib_available():
        log_fn(
            "[yellow]forensics[/yellow] ufedlib-py not installed — skipping UFDR parse "
            "(uv add ufedlib-py && uv sync)"
        )
        summary["skipped_reason"] = "ufedlib-py not installed"
        return summary

    ufdrs = find_ufdr_files(root, limit=limit)
    summary["ufdr_found"] = len(ufdrs)
    if not ufdrs:
        log_fn("[cyan]forensics[/cyan] no .ufdr packages found")
        return summary

    log_fn(f"[cyan]forensics[/cyan] parsing {len(ufdrs)} UFDR package(s)")
    for item in ufdrs:
        rel = str(item["rel_path"])
        ufdr_path = (root / rel).resolve()
        cache_file = _cache_dir(root) / f"{_cache_key(ufdr_path)}.json"
        had_cache = cache_file.is_file() and not force
        try:
            payload = parse_ufdr_chats_for_case(root, rel, use_cache=not force)
            chats = int(payload.get("chat_count") or 0)
            msgs = int(payload.get("message_count") or 0)
            directory_size = int(payload.get("directory_size") or 0)
            if had_cache:
                summary["ufdr_cached"] += 1
                verb = "cache"
            else:
                summary["ufdr_parsed"] += 1
                verb = "parsed"
            summary["chat_count"] += chats
            summary["message_count"] += msgs
            summary["directory_size"] = max(summary["directory_size"], directory_size)
            summary["sources"].append(
                {
                    "rel_path": rel,
                    "name": item.get("name"),
                    "size": item.get("size"),
                    "chat_count": chats,
                    "message_count": msgs,
                    "directory_size": directory_size,
                    "apps": payload.get("sources") or [],
                    "from_cache": had_cache,
                }
            )
            log_fn(
                f"[green]forensics[/green] {verb} {rel} — "
                f"{chats} chats / {msgs} messages"
                + (f" / {directory_size} contact keys" if directory_size else "")
            )
        except Exception as e:
            summary["ufdr_errors"] += 1
            summary["errors"].append({"rel_path": rel, "error": str(e)[:500]})
            log_fn(f"[red]forensics[/red] {rel}: {e}")

    try:
        out = _cache_dir(root).parent / "ingest_summary.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "schema": "marshaloss.forensics_ingest.v1",
                    "root": str(root),
                    **summary,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        summary["summary_path"] = out.as_posix()
    except Exception as e:
        log.warning("forensics ingest summary write failed: %s", e)

    return summary
