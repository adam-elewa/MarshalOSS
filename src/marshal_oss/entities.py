"""
File-level AI entities + tags (last ingest pass).

Covers **full extracted text** per file:
- Short files → one call
- Long files → map over segments, then one reduce that **canonicalizes**
  (e.g. Smith / J. Smith / Jane Smith → canonical "Jane Smith" + aliases)

Not per-chunk indexing — authoritative for UX filters; search still uses FTS/embeddings.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

from .config import Settings
from .quality import join_chunk_text

LogFn = Callable[[str], None]

_MAP_PROMPT = """You are indexing a legal-discovery file for defense counsel.
From this TEXT SEGMENT (part of a larger file), return ONLY compact JSON (no markdown):
{
  "entities": [
    {"type": "person"|"org"|"location"|"officer"|"date"|"bates"|"other",
     "value": "name or phrase as written",
     "note": "optional short"}
  ],
  "tags": ["short_snake_or_phrase, max 12"]
}
Rules: only facts grounded in this segment; prefer names as written; skip empties.
"""

_SINGLE_PROMPT = """You are indexing a legal-discovery file for defense counsel.
From the FULL extracted text below, return ONLY compact JSON (no markdown):
{
  "entities": [
    {"type": "person"|"org"|"location"|"officer"|"date"|"bates"|"other",
     "canonical": "preferred full form",
     "aliases": ["other spellings/short forms seen in text"],
     "note": "optional short"}
  ],
  "tags": ["short_snake_or_phrase tags, max 12, e.g. bodycam, dui, use_of_force"]
}
Canonicalization (important):
- Merge the same real-world referent into ONE entity.
- Example: "Smith", "J. Smith", "Jane Smith", "Ms. Smith" → canonical "Jane Smith",
  aliases ["Smith", "J. Smith", "Ms. Smith"].
- Prefer the most complete proper name as canonical when clear from context.
- Same for orgs (short form / full name when both appear).
- Only facts grounded in the text; skip empty arrays if nothing clear.
"""

_REDUCE_PROMPT = """You are merging entity extractions from segments of ONE legal-discovery file.
Given the candidate lists below (possibly overlapping/noisy), return ONLY compact JSON:
{
  "entities": [
    {"type": "person"|"org"|"location"|"officer"|"date"|"bates"|"other",
     "canonical": "preferred full form",
     "aliases": ["other forms"],
     "note": "optional short"}
  ],
  "tags": ["merged short tags, max 12"]
}
Canonicalization (important):
- Same person/org/place → ONE row. Example: Smith + Jane Smith → canonical "Jane Smith",
  aliases include "Smith".
- Prefer the most complete proper name when the text supports it.
- Drop duplicates and weak one-off noise; keep grounded entities only.
- Union tags; dedupe; max 12.
"""


def _api_keys(settings: Settings) -> tuple[str | None, str | None]:
    oai = (
        settings.openai_api_key
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("MARSHALOSS_OPENAI_API_KEY")
    )
    ant = (
        settings.anthropic_api_key
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("MARSHALOSS_ANTHROPIC_API_KEY")
    )
    return oai, ant


def resolve_entity_client(settings: Settings) -> tuple[str, str, str] | None:
    if not settings.entity_extract:
        return None
    oai, ant = _api_keys(settings)
    pref = (settings.entity_extract_provider or "auto").lower()
    if pref == "openai" and oai:
        return "openai", settings.entity_extract_openai_model, oai
    if pref == "anthropic" and ant:
        return "anthropic", settings.entity_extract_anthropic_model, ant
    if oai:
        return "openai", settings.entity_extract_openai_model, oai
    if ant:
        return "anthropic", settings.entity_extract_anthropic_model, ant
    return None


def _parse_json_blob(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"entities": [], "tags": []}
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.M).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {"entities": [], "tags": [], "raw": text[:500]}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {"entities": [], "tags": [], "raw": text[:500]}
    if not isinstance(data, dict):
        return {"entities": [], "tags": []}
    ents = data.get("entities") or []
    tags = data.get("tags") or []
    if not isinstance(ents, list):
        ents = []
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip()[:64] for t in tags if str(t).strip()][:12]
    clean_ents: list[dict[str, Any]] = []
    for e in ents[:60]:
        if not isinstance(e, dict):
            continue
        canonical = str(
            e.get("canonical") or e.get("value") or e.get("name") or ""
        ).strip()
        if not canonical:
            continue
        aliases_raw = e.get("aliases") or []
        if isinstance(aliases_raw, str):
            aliases_raw = [aliases_raw]
        if not isinstance(aliases_raw, list):
            aliases_raw = []
        aliases: list[str] = []
        seen = {canonical.lower()}
        for a in aliases_raw:
            s = str(a).strip()[:200]
            if not s or s.lower() in seen:
                continue
            seen.add(s.lower())
            aliases.append(s)
        # legacy "value" different from canonical → alias
        legacy = str(e.get("value") or "").strip()
        if legacy and legacy.lower() not in seen:
            aliases.append(legacy[:200])
        clean_ents.append(
            {
                "type": str(e.get("type") or "other").strip().lower()[:32],
                "canonical": canonical[:200],
                "aliases": aliases[:20],
                "note": str(e.get("note") or "").strip()[:200] or None,
            }
        )
    return {"entities": clean_ents, "tags": tags}


def _segment_text(body: str, segment_chars: int) -> list[str]:
    """Split full text into overlapping segments so names spanning cuts aren't lost."""
    n = len(body)
    if n <= segment_chars:
        return [body]
    overlap = min(400, segment_chars // 10)
    step = max(1000, segment_chars - overlap)
    out: list[str] = []
    i = 0
    while i < n:
        piece = body[i : i + segment_chars]
        if not piece.strip():
            break
        # prefer break near whitespace at end
        if i + segment_chars < n and len(piece) == segment_chars:
            cut = piece.rfind("\n")
            if cut < segment_chars // 3:
                cut = piece.rfind(" ")
            if cut > segment_chars // 3:
                piece = piece[:cut]
        out.append(piece)
        if i + len(piece) >= n:
            break
        i += max(step, len(piece) - overlap)
        if len(out) >= 80:  # hard safety
            out.append(body[i:][:segment_chars])
            break
    return out


def _llm_json(
    provider: str,
    model: str,
    key: str,
    prompt: str,
    *,
    max_tokens: int = 1200,
) -> str:
    if provider == "openai":
        from openai import OpenAI

        kwargs: dict[str, Any] = {
            "model": model,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        # json_object when supported; ignore if model rejects
        try:
            resp = OpenAI(api_key=key).chat.completions.create(
                **kwargs,
                response_format={"type": "json_object"},
            )
        except Exception:
            resp = OpenAI(api_key=key).chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    from anthropic import Anthropic

    resp = Anthropic(api_key=key).messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        getattr(b, "text", "") for b in resp.content if getattr(b, "text", None)
    )


def extract_entities_for_text(
    text: str,
    settings: Settings,
    *,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Full-file entity/tag pass with canonical merge for long docs."""
    _log = log or (lambda _m: None)
    client = resolve_entity_client(settings)
    if client is None:
        return {"entities": [], "tags": [], "skipped": "no_client"}

    body = (text or "").strip()
    if len(body) < 40:
        return {"entities": [], "tags": [], "skipped": "too_short"}

    # Optional hard ceiling (0 = no ceiling — use all extracted text)
    max_total = int(settings.entity_extract_max_chars)
    truncated = False
    if max_total > 0 and len(body) > max_total:
        body = body[:max_total]
        truncated = True

    segment = max(2000, int(settings.entity_extract_segment_chars))
    provider, model, key = client
    segments = _segment_text(body, segment)

    try:
        if len(segments) == 1:
            prompt = f"{_SINGLE_PROMPT}\n\n---\n{segments[0]}\n---"
            raw = _llm_json(provider, model, key, prompt, max_tokens=1200)
            parsed = _parse_json_blob(raw)
            parsed["provider"] = provider
            parsed["model"] = model
            parsed["segments"] = 1
            parsed["chars"] = len(body)
            if truncated:
                parsed["truncated"] = True
            return parsed

        # Map: one cheap call per segment
        candidates: list[dict[str, Any]] = []
        tag_bag: list[str] = []
        for idx, seg in enumerate(segments):
            prompt = (
                f"{_MAP_PROMPT}\n\nSEGMENT {idx + 1}/{len(segments)}\n---\n{seg}\n---"
            )
            raw = _llm_json(provider, model, key, prompt, max_tokens=800)
            part = _parse_json_blob(raw)
            for e in part.get("entities") or []:
                # flatten to value for reduce input
                candidates.append(
                    {
                        "type": e.get("type"),
                        "value": e.get("canonical"),
                        "aliases": e.get("aliases") or [],
                        "note": e.get("note"),
                        "segment": idx + 1,
                    }
                )
            tag_bag.extend(part.get("tags") or [])
            _log(
                f"[dim]entity map {idx + 1}/{len(segments)} "
                f"(+{len(part.get('entities') or [])} ents)[/dim]"
            )

        # Reduce: canonicalize across the file
        reduce_payload = json.dumps(
            {"candidates": candidates[:200], "tags": tag_bag[:80]},
            ensure_ascii=False,
        )
        reduce_prompt = f"{_REDUCE_PROMPT}\n\n---\n{reduce_payload}\n---"
        raw = _llm_json(provider, model, key, reduce_prompt, max_tokens=1400)
        parsed = _parse_json_blob(raw)
        parsed["provider"] = provider
        parsed["model"] = model
        parsed["segments"] = len(segments)
        parsed["chars"] = len(body)
        parsed["map_candidates"] = len(candidates)
        if truncated:
            parsed["truncated"] = True
        return parsed
    except Exception as e:
        _log(f"[yellow]entity extract failed[/yellow]: {e}")
        return {"entities": [], "tags": [], "error": str(e)[:300]}


def extract_entities_for_chunks(
    chunks: list[dict[str, Any]],
    settings: Settings,
    *,
    log: LogFn | None = None,
) -> dict[str, Any]:
    return extract_entities_for_text(join_chunk_text(chunks), settings, log=log)
