"""Cellebrite (UFDR) chat access for MCP clients such as Cowork.

Pure functions over the parsed UFDR payload (see forensics_ufdr.parse_ufdr_chats_for_case)
so they can be tested without a server. Every list is paged and capped; responses say
plainly when they were cut short and how to get the rest.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Hard caps per call. Keep a single response comfortably inside a model's context.
MAX_CHATS = 200
MAX_MESSAGES = 300
MAX_HITS = 100
MAX_BODY_CHARS = 2000
RESPONSE_BUDGET_CHARS = 45_000


# ----------------------------------------------------------------------------- time


def resolve_tz(tz: str | None, case_tz: str | None) -> tuple[ZoneInfo | timezone, str]:
    """Pick the zone used to read date_from/date_to and to show local_time."""
    name = (tz or case_tz or "").strip()
    if not name:
        return timezone.utc, "UTC (no case timezone set; pass tz='America/New_York' etc. to change)"
    if name.upper() in ("UTC", "Z", "GMT"):
        return timezone.utc, "UTC"
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown timezone: {name!r} (use an IANA name like 'America/New_York')") from None


def _parse_day(s: str | None, field: str) -> date | None:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{field} must be a date like 2020-07-31 or 7/31/2020, got {s!r}")


def day_bounds(date_from: str | None, date_to: str | None, tzinfo) -> tuple[float | None, float | None]:
    """[lo, hi) epoch seconds covering whole calendar days in tzinfo; date_to is inclusive."""
    f = _parse_day(date_from, "date_from")
    t = _parse_day(date_to, "date_to")
    if f and t and t < f:
        raise ValueError("date_to is before date_from")
    lo = datetime.combine(f, time.min, tzinfo).timestamp() if f else None
    hi = datetime.combine(t + timedelta(days=1), time.min, tzinfo).timestamp() if t else None
    return lo, hi


def msg_epoch(ts: Any) -> float | None:
    s = str(ts or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def local_str(epoch: float | None, tzinfo) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tzinfo).strftime("%Y-%m-%d %H:%M:%S %Z").strip()


def in_range(epoch: float | None, lo: float | None, hi: float | None) -> bool:
    if lo is None and hi is None:
        return True
    if epoch is None:
        return False
    return (lo is None or epoch >= lo) and (hi is None or epoch < hi)


# ----------------------------------------------------------------------------- paging


def page_within_budget(
    items: Iterable[dict[str, Any]],
    total: int,
    offset: int,
    limit: int,
    cap: int,
    *,
    what: str,
    next_call: str,
) -> dict[str, Any]:
    """Take up to `limit` (≤ cap) items that fit in the response budget, with a clear notice."""
    asked = max(1, int(limit or 1))
    limit = min(asked, cap)
    out: list[dict[str, Any]] = []
    used = 0
    budget_hit = False
    for it in items:
        if len(out) >= limit:
            break
        size = len(json.dumps(it, default=str))
        if out and used + size > RESPONSE_BUDGET_CHARS:
            budget_hit = True
            break
        out.append(it)
        used += size
    end = offset + len(out)
    has_more = end < total
    notice = None
    if has_more:
        why = []
        if asked > cap:
            why.append(f"limit is capped at {cap}")
        if budget_hit:
            why.append("response size budget reached")
        notice = (
            f"TRUNCATED: showing {what} {offset + 1 if out else offset}–{end} of {total}"
            + (f" ({'; '.join(why)})" if why else "")
            + f". Call again with offset={end} to continue: {next_call.replace('{offset}', str(end))}"
        )
    return {
        "items": out,
        "total": total,
        "offset": offset,
        "returned": len(out),
        "limit": limit,
        "has_more": has_more,
        "next_offset": end if has_more else None,
        "truncated": has_more,
        "notice": notice,
    }


def _clip(s: str, n: int) -> tuple[str, bool]:
    s = s or ""
    return (s, False) if len(s) <= n else (s[:n] + "…", True)


# ----------------------------------------------------------------------------- tools


def _chat_matches(c: dict[str, Any], q: str, q_digits: str) -> bool:
    blob = " ".join(
        str(x or "")
        for x in [c.get("title"), c.get("name"), c.get("handle"), c.get("source"), c.get("preview")]
        + list(c.get("participants") or [])
        + list(c.get("counterparties") or [])
    )
    if q and q in blob.lower():
        return True
    if q_digits and len(q_digits) >= 4:
        return q_digits[-10:] in re.sub(r"\D", "", blob)
    return False


def list_chats(
    payloads: list[dict[str, Any]],
    *,
    app: str | None = None,
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    tz: str | None = None,
    case_tz: str | None = None,
    include_empty: bool = False,
    sort: str = "recent",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    tzinfo, tz_label = resolve_tz(tz, case_tz)
    lo, hi = day_bounds(date_from, date_to, tzinfo)
    ranged = lo is not None or hi is not None
    q = (query or "").strip().lower()
    q_digits = re.sub(r"\D", "", q)
    app_l = (app or "").strip().lower()
    if sort not in ("recent", "messages", "oldest"):
        raise ValueError("sort must be recent|messages|oldest")

    rows: list[dict[str, Any]] = []
    apps: dict[str, int] = {}
    for p in payloads:
        for c in p.get("chats") or []:
            msgs = c.get("messages") or []
            n_all = int(c.get("message_count") or len(msgs))
            if not include_empty and n_all <= 0:
                continue
            if app_l and app_l not in str(c.get("source") or "").lower():
                continue
            if q and not _chat_matches(c, q, q_digits):
                continue
            n_in = sum(1 for m in msgs if in_range(msg_epoch(m.get("timestamp")), lo, hi)) if ranged else n_all
            if ranged and n_in == 0:
                continue
            apps[str(c.get("source") or "Unknown")] = apps.get(str(c.get("source") or "Unknown"), 0) + 1
            preview, _ = _clip(str(c.get("preview") or ""), 160)
            rows.append(
                {
                    "path": p.get("rel_path"),
                    "chat_id": c.get("id"),
                    "title": c.get("title") or c.get("name"),
                    "app": c.get("source"),
                    "handle": c.get("handle") or "",
                    "participants": (c.get("participants") or [])[:12],
                    "participant_count": len(c.get("participants") or []),
                    "first_message": c.get("start"),
                    "last_message": c.get("last_activity"),
                    "message_count": n_all,
                    "messages_in_range": n_in if ranged else None,
                    "preview": preview,
                }
            )
    if sort == "messages":
        rows.sort(key=lambda r: -(r["messages_in_range"] if ranged else r["message_count"]))
    else:
        rows.sort(key=lambda r: str(r.get("last_message") or ""), reverse=(sort == "recent"))

    offset = max(0, int(offset or 0))
    pg = page_within_budget(
        rows[offset:], len(rows), offset, limit, MAX_CHATS,
        what="chats", next_call="cellebrite_chats(..., offset={offset})",
    )
    return {
        "chats": pg.pop("items"),
        **pg,
        "apps": dict(sorted(apps.items(), key=lambda kv: -kv[1])),
        "filters": {"app": app, "query": query, "date_from": date_from, "date_to": date_to, "timezone": tz_label},
        "how_to_read": "Read a thread with cellebrite_messages(path, chat_id). Search message text with cellebrite_search.",
    }


def _msg_out(m: dict[str, Any], idx: int, tzinfo) -> dict[str, Any]:
    body, clipped = _clip(str(m.get("body") or ""), MAX_BODY_CHARS)
    ep = msg_epoch(m.get("timestamp"))
    out = {
        "index": idx,
        "timestamp_utc": m.get("timestamp") or None,
        "local_time": local_str(ep, tzinfo),
        "from": m.get("from") or "",
        "status": m.get("status") or "",
        "body": body,
    }
    if clipped:
        out["body_truncated"] = True
        out["body_total_chars"] = len(str(m.get("body") or ""))
    if not body:
        out["body"] = "[no text — attachment, reaction or system message]"
    return out


def read_messages(
    payload: dict[str, Any],
    chat: dict[str, Any],
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    tz: str | None = None,
    case_tz: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    tzinfo, tz_label = resolve_tz(tz, case_tz)
    lo, hi = day_bounds(date_from, date_to, tzinfo)
    msgs = chat.get("messages") or []
    picked = [(i, m) for i, m in enumerate(msgs) if in_range(msg_epoch(m.get("timestamp")), lo, hi)]
    offset = max(0, int(offset or 0))
    path = payload.get("rel_path")
    cid = chat.get("id")
    pg = page_within_budget(
        (_msg_out(m, i, tzinfo) for i, m in picked[offset:]),
        len(picked), offset, limit, MAX_MESSAGES,
        what="messages",
        next_call=f"cellebrite_messages(path={path!r}, chat_id={cid!r}"
        + (f", date_from={date_from!r}" if date_from else "")
        + (f", date_to={date_to!r}" if date_to else "")
        + ", offset={offset})",
    )
    return {
        "path": path,
        "chat_id": cid,
        "title": chat.get("title") or chat.get("name"),
        "app": chat.get("source"),
        "participants": chat.get("participants") or [],
        "phone_owners": payload.get("owners") or [],
        "message_count": len(msgs),
        "messages": pg.pop("items"),
        **pg,
        "filters": {"date_from": date_from, "date_to": date_to, "timezone": tz_label},
        "note": "'Me' = the phone's owner. index is the message's position in the whole thread "
        "(stable across calls; pass offset=index with no date filter to read around it).",
    }


def search_messages(
    payloads: list[dict[str, Any]],
    query: str,
    *,
    app: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    tz: str | None = None,
    case_tz: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        raise ValueError("query is required")
    tzinfo, tz_label = resolve_tz(tz, case_tz)
    lo, hi = day_bounds(date_from, date_to, tzinfo)
    terms = [t for t in re.split(r"\s+", q.lower().strip('"')) if t]
    phrase = q.lower().strip('"') if q.startswith('"') and q.endswith('"') and len(q) > 2 else None
    q_digits = re.sub(r"\D", "", q)
    digits_mode = len(q_digits) >= 7 and len(q_digits) >= len(re.sub(r"[\s()+\-.]", "", q)) - 1
    app_l = (app or "").strip().lower()

    hits: list[dict[str, Any]] = []
    for p in payloads:
        for c in p.get("chats") or []:
            if app_l and app_l not in str(c.get("source") or "").lower():
                continue
            for i, m in enumerate(c.get("messages") or []):
                ep = msg_epoch(m.get("timestamp"))
                if not in_range(ep, lo, hi):
                    continue
                body = str(m.get("body") or "")
                hay = f"{body} {m.get('from') or ''}".lower()
                if digits_mode:
                    ok = q_digits[-10:] in re.sub(r"\D", "", hay)
                elif phrase:
                    ok = phrase in hay
                else:
                    ok = all(t in hay for t in terms)
                if not ok:
                    continue
                pos = hay.find((phrase or terms[0]) if not digits_mode else "")
                start = max(0, pos - 80) if pos >= 0 else 0
                snippet = ("…" if start else "") + body[start : start + 240] + ("…" if len(body) > start + 240 else "")
                hits.append(
                    {
                        "path": p.get("rel_path"),
                        "chat_id": c.get("id"),
                        "chat_title": c.get("title") or c.get("name"),
                        "app": c.get("source"),
                        "index": i,
                        "timestamp_utc": m.get("timestamp") or None,
                        "local_time": local_str(ep, tzinfo),
                        "from": m.get("from") or "",
                        "snippet": snippet,
                        "_ep": ep or 0,
                    }
                )
    hits.sort(key=lambda h: h["_ep"])
    for h in hits:
        h.pop("_ep", None)
    offset = max(0, int(offset or 0))
    pg = page_within_budget(
        hits[offset:], len(hits), offset, limit, MAX_HITS,
        what="matches", next_call=f"cellebrite_search(query={q!r}, ..., offset={{offset}})",
    )
    return {
        "query": q,
        "match_mode": "phone digits" if digits_mode else ("exact phrase" if phrase else "all words (case-insensitive)"),
        "hits": pg.pop("items"),
        **pg,
        "filters": {"app": app, "date_from": date_from, "date_to": date_to, "timezone": tz_label},
        "how_to_read": "Hits are oldest first. For context call cellebrite_messages(path, chat_id, "
        "offset=max(0, index-10), limit=25).",
    }
