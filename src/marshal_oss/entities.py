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

_ENTITY_NOISE_RULE = (
    "Substantive-only rules (critical — keep the case index SMALL):\n"
    "- Entities allowed types ONLY: person, officer, org, location.\n"
    "- Do NOT emit date, time, bates, or other. Bates/dates live elsewhere.\n"
    "- People/officers: real proper names only (e.g. \"David Eli Jones\", \"Trooper Baca\").\n"
    "  NEVER: Speaker A/B, badge numbers alone (\"7867\"), clothing descriptions\n"
    "  (\"man in green shirt\"), camera products (\"Axon Body 3\").\n"
    "- Orgs: named agencies/courts/firms (\"Springfield Police Department\", \"U.S. District Court for the District of Oregon\"), not generic roles.\n"
    "- Locations: specific places tied to the matter (city/county/address/landmark).\n"
    "  Skip bare US state names alone (\"Georgia\") unless part of a fuller place.\n"
    "- Per file: at most 8 entities total after merge; prefer the most important names.\n"
    "- Tags: at most 5, snake_case, case-filter themes a lawyer would actually use\n"
    "  (e.g. bodycam, dashcam, traffic_stop, firearm, dui, arrest).\n"
    "  NEVER atmospheric/visual/meta tags: daytime, nighttime, overcast_weather,\n"
    "  dim_lighting, timestamp, video, evidence, court, legal_discovery, SUV,\n"
    "  highway_scene, emergency_response, active_case, defendant, prosecutor.\n"
)

# Clock / overlay stamps mislabeled as Bates (common on Axon / fleet video OCR).
_CLOCKISH = re.compile(
    r"(?ix)^\s*"
    r"\d{1,2}:\d{2}(?::\d{2})?"
    r"(?:\.\d+)?"
    r"(?:\s*(?:AM|PM|A\.M\.|P\.M\.|UTC|GMT|Z))?"
    r"\s*$"
)

# ASR diarization labels, not real people.
_SPEAKER_LABEL = re.compile(
    r"(?ix)^\s*"
    r"(?:"
    r"speaker\s*[-_]?\s*[a-z0-9]+"
    r"|spk\s*[-_]?\s*[a-z0-9]+"
    r"|speaker\s*[-_]?\s*\d+"
    r"|unknown\s+speaker"
    r")"
    r"\s*$"
)


def looks_like_video_timestamp(value: str) -> bool:
    """True for burned-in video clocks, not production Bates ids."""
    s = re.sub(r"\s+", " ", (value or "").strip())
    if not s:
        return False
    return bool(_CLOCKISH.match(s))


def looks_like_diarization_speaker(value: str) -> bool:
    """True for transcript diarization labels like Speaker A / SPK_1."""
    s = re.sub(r"\s+", " ", (value or "").strip())
    if not s:
        return False
    return bool(_SPEAKER_LABEL.match(s))


# Types we keep for case overview / future ingest. Dates, times, Bates are noise here.
_SUBSTANTIVE_TYPES = frozenset({"person", "officer", "org", "location"})

# NOTE: do not use the (?x) flag here — `#` would start a regex comment.
_BADGE_ONLY = re.compile(
    r"(?i)^\s*(?:officer|ofc\.?|trooper|tpr\.?|badge)?\s*#?\s*\d{2,6}\s*$"
)
_DESCRIPTIVE_PERSON = re.compile(
    r"(?i)\b(?:man|woman|male|female|person|individual|suspect|subject)\b.+\b(?:in|wearing|with)\b"
    r"|\b(?:in|wearing)\b.+\b(?:uniform|shirt|jacket|hoodie|glasses)\b"
)
_DEVICE_OR_PRODUCT = re.compile(
    r"(?i)\b(?:axon|body\s*[23]|fleet\s*[23]|mobile[\s-]?vision|watchguard|l-?3)\b"
)
_BARE_STATE = re.compile(
    r"(?i)^\s*(?:alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|"
    r"florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|"
    r"maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|"
    r"nevada|new\s+hampshire|new\s+jersey|new\s+mexico|new\s+york|north\s+carolina|"
    r"north\s+dakota|ohio|oklahoma|oregon|pennsylvania|rhode\s+island|south\s+carolina|"
    r"south\s+dakota|tennessee|texas|utah|vermont|virginia|washington|west\s+virginia|"
    r"wisconsin|wyoming|district\s+of\s+columbia)\s*$"
)

_TAG_DENY = frozenset(
    {
        "daytime",
        "nighttime",
        "overcast_weather",
        "dim_lighting",
        "timestamp",
        "video",
        "evidence",
        "court",
        "legal_discovery",
        "suv",
        "highway_scene",
        "emergency_response",
        "active_case",
        "defendant",
        "prosecutor",
        "public_defender",
        "police_vehicle",
        "court_case",
        "criminal_case",
        "court_date",
        "court_order",
        "impairment",
        "aliases",
        "arguments",
        "back_seat_area",
        "case_creation",
        "coordinates",
        "defendant_info",
        "criminal",
        "damaged_vehicle",
        "bus",
        "law_enforcement",
        "court_records",
        "police_report",
        "arrest_report",  # prefer arrest / bodycam etc.
    }
)
_MAX_TAGS_PER_FILE = 5
_MAX_ENTITIES_PER_FILE = 8


def is_noise_entity(etype: str, value: str) -> bool:
    """Drop non-substantive entities (dates/Bates/times, weak people, bare states)."""
    et = (etype or "").strip().lower()
    s = re.sub(r"\s+", " ", (value or "").strip())
    if not s:
        return True
    if looks_like_diarization_speaker(s):
        return True
    if looks_like_video_timestamp(s):
        return True
    if et not in _SUBSTANTIVE_TYPES:
        return True
    if et in ("person", "officer"):
        if _BADGE_ONLY.match(s):
            return True
        if _DESCRIPTIVE_PERSON.search(s):
            return True
        if _DEVICE_OR_PRODUCT.search(s):
            return True
        # Need a real name signal: at least one letter token that isn't a title-only blob
        tokens = person_name_tokens(s)
        if not tokens:
            return True
        if len(tokens) == 1 and tokens[0].isdigit():
            return True
        if len(tokens) == 1 and len(tokens[0]) <= 2:
            return True
    if et == "location" and _BARE_STATE.match(s):
        return True
    if et == "org" and _DEVICE_OR_PRODUCT.search(s):
        return True
    return False


def normalize_tag(tag: str) -> str:
    s = re.sub(r"[\s\-]+", "_", (tag or "").strip().lower())
    s = re.sub(r"[^\w]+", "", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:64]


def is_noise_tag(tag: str) -> bool:
    t = normalize_tag(tag)
    if not t or len(t) < 3:
        return True
    if t in _TAG_DENY:
        return True
    # Bare jurisdictions say where, not what: "new_jersey", "essex_county"
    if _BARE_STATE.match(t.replace("_", " ")) or t.endswith("_county"):
        return True
    # Purely atmospheric / lighting / weather leftovers
    if any(x in t for x in ("lighting", "weather", "daytime", "nighttime", "overcast")):
        return True
    return False


def filter_tags(tags: list[Any] | None, *, limit: int = _MAX_TAGS_PER_FILE) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        label = raw if isinstance(raw, str) else str(
            (raw or {}).get("name") or (raw or {}).get("tag") or (raw or {}).get("label") or ""
        )
        t = normalize_tag(label)
        if not t or is_noise_tag(t) or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out


_TITLE_PREFIX = re.compile(
    r"(?ix)^\s*(?:the\s+)?(?:honorable|hon\.?|officer|ofc\.?|det\.?|detective|"
    r"trooper|tpr\.?|sgt\.?|sergeant|lt\.?|lieutenant|cpt\.?|captain|chief|"
    r"agent|investigator|inv\.?|dr\.?|mr\.?|mrs\.?|ms\.?|miss)\s+"
)
_ROMAN_SUFFIX = re.compile(r"(?ix)\s+(?:jr\.?|sr\.?|ii|iii|iv|v)$")


def person_name_tokens(value: str) -> list[str]:
    """Tokenize a person/officer name for matching (titles stripped)."""
    s = re.sub(r"\s+", " ", (value or "").strip())
    if not s:
        return []
    s = _TITLE_PREFIX.sub("", s)
    s = _ROMAN_SUFFIX.sub("", s)
    s = s.replace(",", " ")
    # Keep letters/digits; turn punctuation into spaces (E. → E)
    s = re.sub(r"[^\w\s-]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return [t for t in s.split(" ") if t]


def person_match_key(value: str) -> tuple[str, ...] | None:
    """
    Match key for collapsing name variants.

    David Jones / David E. Jones / David Eli Jones → ('david', 'jones')
    Single-token names stay as themselves; not merged into others.
    """
    tokens = person_name_tokens(value)
    if not tokens:
        return None
    if len(tokens) == 1:
        return (tokens[0].lower(),)
    first = tokens[0].lower()
    last = tokens[-1].lower()
    return (first, last)


def person_names_compatible(a: str, b: str) -> bool:
    """True when two labels are the same person under middle-initial / title noise."""
    ka, kb = person_match_key(a), person_match_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    # Don't merge bare surnames/first-only into full names.
    if len(ka) == 1 or len(kb) == 1:
        return False
    # Same last name; first name is initial of the other (J Duran ↔ Juan Duran).
    if ka[1] != kb[1]:
        return False
    fa, fb = ka[0], kb[0]
    if fa == fb:
        return True
    if len(fa) == 1 and fb.startswith(fa):
        return True
    if len(fb) == 1 and fa.startswith(fb):
        return True
    return False


def prefer_person_canonical(a: str, b: str) -> str:
    """Prefer the more complete display form (middle name > initial > none)."""
    ta, tb = person_name_tokens(a), person_name_tokens(b)
    # More tokens wins (David Eli Jones over David Jones / David E Jones).
    if len(ta) != len(tb):
        return a if len(ta) > len(tb) else b
    # Prefer forms without single-letter middle initials when same length... 
    # Actually same length: prefer fewer single-letter tokens (Eli > E)
    def initialish(tokens: list[str]) -> int:
        return sum(1 for t in tokens[1:-1] if len(t) == 1)

    ia, ib = initialish(ta), initialish(tb)
    if ia != ib:
        return a if ia < ib else b
    # Prefer longer raw string / more punctuation-complete
    if len(a) != len(b):
        return a if len(a) > len(b) else b
    return a if a <= b else b


def merge_person_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse person/officer variants within one file's entity list."""
    others: list[dict[str, Any]] = []
    # clusters: list of {canonical, aliases, types, note}
    clusters: list[dict[str, Any]] = []

    def add_person(etype: str, canonical: str, aliases: list[str], note: str | None) -> None:
        names = [canonical, *aliases]
        for c in clusters:
            if any(
                person_names_compatible(canonical, str(x))
                or any(person_names_compatible(str(a), str(x)) for a in aliases)
                for x in [c["canonical"], *c["aliases"]]
            ):
                # merge into cluster
                c["type"] = "officer" if ("officer" in (c["type"], etype)) else c["type"]
                for n in names:
                    n = str(n).strip()
                    if not n:
                        continue
                    if person_names_compatible(n, c["canonical"]) or any(
                        person_names_compatible(n, a) for a in c["aliases"]
                    ):
                        c["canonical"] = prefer_person_canonical(c["canonical"], n)
                        if n.lower() != c["canonical"].lower() and n not in c["aliases"]:
                            # will re-derive aliases below
                            c["aliases"].append(n)
                if note and not c.get("note"):
                    c["note"] = note
                return
        clusters.append(
            {
                "type": etype,
                "canonical": canonical,
                "aliases": [a for a in aliases if a and a.lower() != canonical.lower()],
                "note": note,
            }
        )

    for e in entities:
        etype = str(e.get("type") or "other").strip().lower()
        if etype not in ("person", "officer"):
            others.append(e)
            continue
        canonical = str(e.get("canonical") or "").strip()
        if not canonical:
            continue
        aliases = [str(a).strip() for a in (e.get("aliases") or []) if str(a).strip()]
        add_person(etype, canonical, aliases, e.get("note"))

    out = list(others)
    for c in clusters:
        # Dedupe aliases; drop ones equal to canonical; keep compatible variants only
        seen = {c["canonical"].lower()}
        aliases: list[str] = []
        for a in c["aliases"]:
            al = a.lower()
            if al in seen:
                continue
            if not person_names_compatible(a, c["canonical"]):
                continue
            seen.add(al)
            if a != c["canonical"]:
                aliases.append(a)
        out.append(
            {
                "type": c["type"],
                "canonical": c["canonical"][:200],
                "aliases": aliases[:20],
                "note": c.get("note"),
            }
        )
    return out


def collapse_person_counts(
    entity_counts: dict[tuple[str, str], int] | Any,
) -> dict[tuple[str, str], int]:
    """
    Collapse (person|officer, label) counters across a case so
    David Jones / David E. Jones / David Eli Jones share one row.
    """
    # items: list of {type, canonical, count, aliases}
    people: list[dict[str, Any]] = []
    other: dict[tuple[str, str], int] = {}
    for (et, label), n in entity_counts.items():
        if et not in ("person", "officer") or not label:
            other[(et, label)] = other.get((et, label), 0) + int(n)
            continue
        merged = False
        for p in people:
            if person_names_compatible(label, p["canonical"]) or any(
                person_names_compatible(label, a) for a in p["aliases"]
            ):
                p["count"] += int(n)
                p["type"] = "officer" if "officer" in (p["type"], et) else p["type"]
                p["canonical"] = prefer_person_canonical(p["canonical"], label)
                if label.lower() != p["canonical"].lower():
                    p["aliases"].append(label)
                merged = True
                break
        if not merged:
            people.append(
                {"type": et, "canonical": label, "count": int(n), "aliases": []}
            )
    out: dict[tuple[str, str], int] = dict(other)
    for p in people:
        out[(p["type"], p["canonical"])] = out.get((p["type"], p["canonical"]), 0) + p["count"]
    return out


_PERSON_MERGE_RULE = (
    "Person merge rules (critical):\n"
    "- Middle initials / middle names are the SAME person: "
    '"David Jones", "David E. Jones", "David E Jones", "David Eli Jones" → '
    'ONE entity, canonical the most complete form (prefer "David Eli Jones"), '
    "others in aliases.\n"
    "- Titles do not create a new person: \"Trooper Baca\" / \"Baca\" when clearly "
    "the same referent → merge.\n"
    "- Do NOT invent merges across different surnames.\n"
)


_MAP_PROMPT = (
    "You are indexing a legal-discovery file for defense counsel.\n"
    "From this TEXT SEGMENT (part of a larger file), return ONLY compact JSON (no markdown):\n"
    "{\n"
    '  "entities": [\n'
    '    {"type": "person"|"officer"|"org"|"location",\n'
    '     "value": "name or phrase as written",\n'
    '     "note": "optional short"}\n'
    "  ],\n"
    '  "tags": ["snake_case themes, max 5"]\n'
    "}\n"
    "Be selective: only high-signal names and tags. Skip empties.\n"
    + _ENTITY_NOISE_RULE
    + _PERSON_MERGE_RULE
)

_SINGLE_PROMPT = (
    "You are indexing a legal-discovery file for defense counsel.\n"
    "From the FULL extracted text below, return ONLY compact JSON (no markdown):\n"
    "{\n"
    '  "entities": [\n'
    '    {"type": "person"|"officer"|"org"|"location",\n'
    '     "canonical": "preferred full form",\n'
    '     "aliases": ["other spellings/short forms seen in text"],\n'
    '     "note": "optional short"}\n'
    "  ],\n"
    '  "tags": ["snake_case themes, max 5, e.g. bodycam, traffic_stop, firearm"]\n'
    "}\n"
    "Canonicalization (important):\n"
    "- Merge the same real-world referent into ONE entity.\n"
    '- Example: "Smith", "J. Smith", "Jane Smith", "Ms. Smith" → canonical "Jane Smith",\n'
    '  aliases ["Smith", "J. Smith", "Ms. Smith"].\n'
    "- Prefer the most complete proper name as canonical when clear from context.\n"
    "- Same for orgs (short form / full name when both appear).\n"
    "- Be selective — a typical file should yield a handful of entities, not dozens.\n"
    + _ENTITY_NOISE_RULE
    + _PERSON_MERGE_RULE
)

_REDUCE_PROMPT = (
    "You are merging entity extractions from segments of ONE legal-discovery file.\n"
    "Given the candidate lists below (possibly overlapping/noisy), return ONLY compact JSON:\n"
    "{\n"
    '  "entities": [\n'
    '    {"type": "person"|"officer"|"org"|"location",\n'
    '     "canonical": "preferred full form",\n'
    '     "aliases": ["other forms"],\n'
    '     "note": "optional short"}\n'
    "  ],\n"
    '  "tags": ["merged snake_case themes, max 5"]\n'
    "}\n"
    "Canonicalization (important):\n"
    '- Same person/org/place → ONE row. Example: Smith + Jane Smith → canonical "Jane Smith",\n'
    '  aliases include "Smith".\n'
    "- Prefer the most complete proper name when the text supports it.\n"
    "- Aggressively drop weak one-off noise; keep only high-signal entities.\n"
    "- Cap at 8 entities and 5 tags for the whole file.\n"
    + _ENTITY_NOISE_RULE
    + _PERSON_MERGE_RULE
)


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


def scrub_noise_entities(entities: list[Any] | None) -> list[Any]:
    """Remove non-substantive entities from stored meta (for cleanup + parse)."""
    out: list[Any] = []
    for e in entities or []:
        if isinstance(e, dict):
            typ = str(e.get("type") or "").strip().lower()
            label = str(
                e.get("canonical") or e.get("text") or e.get("value") or e.get("name") or ""
            ).strip()
            if is_noise_entity(typ, label):
                continue
            if e.get("aliases"):
                e = {
                    **e,
                    "aliases": [
                        a
                        for a in (e.get("aliases") or [])
                        if not is_noise_entity(typ, str(a))
                    ],
                }
            out.append(e)
        else:
            if is_noise_entity("other", str(e)):
                continue
            out.append(e)
    # Prefer people/orgs/places with names; hard cap after person-merge
    return out[:_MAX_ENTITIES_PER_FILE]


def scrub_file_annotations(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Tighten entities + tags on an existing file meta blob."""
    m = dict(meta or {})
    ents = scrub_noise_entities(m.get("entities") or [])
    ents = merge_person_entities([e for e in ents if isinstance(e, dict)])
    ents = [e for e in ents if not is_noise_entity(str(e.get("type") or ""), str(e.get("canonical") or ""))]
    m["entities"] = ents[:_MAX_ENTITIES_PER_FILE]
    m["tags"] = filter_tags(m.get("tags") or [])
    return m


# Back-compat alias
scrub_timestamp_bates = scrub_noise_entities


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
    tags = filter_tags(tags)
    clean_ents: list[dict[str, Any]] = []
    for e in ents[:40]:
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
        etype = str(e.get("type") or "other").strip().lower()[:32]
        if is_noise_entity(etype, canonical):
            continue
        aliases = [a for a in aliases if not is_noise_entity(etype, a)]
        clean_ents.append(
            {
                "type": etype,
                "canonical": canonical[:200],
                "aliases": aliases[:20],
                "note": str(e.get("note") or "").strip()[:200] or None,
            }
        )
    merged = merge_person_entities(clean_ents)
    merged = [e for e in merged if not is_noise_entity(str(e.get("type") or ""), str(e.get("canonical") or ""))]
    return {"entities": merged[:_MAX_ENTITIES_PER_FILE], "tags": tags}


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
