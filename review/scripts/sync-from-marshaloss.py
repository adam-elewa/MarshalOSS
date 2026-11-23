#!/usr/bin/env python3
"""Push MarshalOSS extracted text, tags, and named entities into Datashare.

Fills document ``content`` / ``tags`` and writes Datashare ``NamedEntity`` child
documents (document Entities tab, Search → Entities filters, Insights counts).

Also rebuilds ``{project}.entities`` StructuredEntity docs (unique mentions
rolled up across the case) for the project-level entities index.

Note: Datashare's sidebar **Entities** item under Tasks is NLP *job* history,
not an entity browser. Project-wide browsing is Search → left filters
(Person / Organization / Location / Email).

Usage:

  python3 review/scripts/sync-from-marshaloss.py \\
    --slug david-jones --project david-jones \\
    --es-via-docker datashare-elasticsearch-1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Datashare NamedEntity.Category
_TYPE_MAP = {
    "person": "PERSON",
    "people": "PERSON",
    "officer": "PERSON",
    "witness": "PERSON",
    "defendant": "PERSON",
    "victim": "PERSON",
    "attorney": "PERSON",
    "organization": "ORGANIZATION",
    "org": "ORGANIZATION",
    "company": "ORGANIZATION",
    "agency": "ORGANIZATION",
    "place": "LOCATION",
    "location": "LOCATION",
    "address": "LOCATION",
    "date": "DATE",
    "email": "EMAIL",
    "money": "MONEY",
    "number": "NUMBER",
    "bates": "NUMBER",
    "id": "NUMBER",
}
_EXTRACTOR = "CORENLP"  # valid Pipeline.Type; IDs stay stable across re-syncs

# FtM-ish schema names for StructuredEntity.entityType
_ENTITY_TYPE = {
    "PERSON": "Person",
    "ORGANIZATION": "Organization",
    "LOCATION": "Address",
    "EMAIL": "Email",
    "DATE": "Date",
    "MONEY": "Payment",
    "NUMBER": "Identifier",
}


def _db_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("MARSHALOSS_DB_PATH")
    if env:
        return Path(env)
    data = Path(os.environ.get("MARSHALOSS_DATA_DIR", Path.home() / ".marshaloss"))
    return data / "marshal_oss.db"


def _es_request(es: str, method: str, path: str, body: dict | None = None, docker: str | None = None):
    url_path = path if path.startswith("/") else f"/{path}"
    payload = None if body is None else json.dumps(body).encode("utf-8")
    if docker:
        cmd = [
            "docker", "exec", "-i", docker,
            "curl", "-s", "-w", "\n%{http_code}", "-X", method,
            f"http://localhost:9200{url_path}",
            "-H", "Content-Type: application/json",
        ]
        if payload is not None:
            cmd += ["--data-binary", "@-"]
            out = subprocess.check_output(cmd, input=payload.decode("utf-8"), text=True)
        else:
            out = subprocess.check_output(cmd, text=True)
        body_text, _, code_s = out.rstrip().rpartition("\n")
        code = int(code_s or "0")
        if code >= 400:
            raise RuntimeError(f"ES {method} {url_path}: {code} {body_text}")
        return json.loads(body_text) if body_text.strip() else {}

    req = urllib.request.Request(
        f"{es.rstrip('/')}{url_path}",
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ES {method} {url_path}: {e.code} {err}") from e


def _scroll_docs(es: str, project: str, docker: str | None) -> list[dict]:
    docs: list[dict] = []
    body = {
        "size": 200,
        "query": {"term": {"type": "Document"}},
        "_source": ["path", "contentTextLength", "tags", "title", "contentType", "nerTags"],
    }
    res = _es_request(es, "POST", f"/{project}/_search?scroll=2m", body, docker)
    scroll_id = res.get("_scroll_id")
    while True:
        hits = res.get("hits", {}).get("hits", [])
        if not hits:
            break
        docs.extend(hits)
        if not scroll_id:
            break
        res = _es_request(
            es, "POST", "/_search/scroll",
            {"scroll": "2m", "scroll_id": scroll_id}, docker,
        )
        scroll_id = res.get("_scroll_id")
    if scroll_id:
        try:
            _es_request(es, "DELETE", "/_search/scroll", {"scroll_id": [scroll_id]}, docker)
        except Exception:
            pass
    return docs


def _rel_from_ds_path(path: str, project: str) -> str:
    marker = f"/data/{project}/"
    if marker in path:
        return path.split(marker, 1)[1]
    parts = path.split("/")
    if project in parts:
        i = parts.index(project)
        return "/".join(parts[i + 1 :])
    return path.lstrip("/")


def _mention_norm(mention: str) -> str:
    # Datashare StringUtils.normalize: unidecode → trim → collapse ws → lower
    try:
        from unidecode import unidecode  # type: ignore

        s = unidecode(mention)
    except Exception:
        s = mention
    return re.sub(r"\s+", " ", s).strip().lower()


def _named_entity_id(document_id: str, offsets: list[int], mention_norm: str) -> str:
    # NamedEntity ctor: SHA-384(documentId|offsets.toString()|extractor|mentionNorm)
    joined = "|".join([document_id, str(offsets), _EXTRACTOR, mention_norm])
    return hashlib.sha384(joined.encode("utf-8")).hexdigest()


def _category(etype: str) -> str:
    key = (etype or "").strip().lower().replace(" ", "_")
    return _TYPE_MAP.get(key, "UNKNOWN")


def _tags_from_meta(meta: dict) -> list[str]:
    tags: list[str] = []
    for t in meta.get("tags") or []:
        t = str(t).strip()
        if t and t not in tags:
            tags.append(t)
    for ent in meta.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        name = (ent.get("canonical") or ent.get("value") or "").strip()
        etype = (ent.get("type") or "entity").strip().lower().replace(" ", "_")
        if not name:
            continue
        label = f"{etype}:{name}"
        if label not in tags:
            tags.append(label)
        if name not in tags and len(name) < 80:
            tags.append(name)
    media = meta.get("media") or {}
    kind = media.get("kind")
    if kind in ("bodycam", "dashcam", "audio", "video") and kind not in tags:
        tags.append(kind)
    return tags[:80]


def _entities_from_meta(meta: dict, text: str) -> list[dict]:
    """Deduped NamedEntity payloads for one document."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for ent in meta.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        mention = (ent.get("canonical") or ent.get("value") or "").strip()
        if not mention or len(mention) > 200:
            continue
        category = _category(str(ent.get("type") or ""))
        key = (category, _mention_norm(mention))
        if key in seen:
            continue
        seen.add(key)
        pos = text.find(mention) if text else -1
        if pos < 0:
            # try case-insensitive
            low = text.lower()
            pos = low.find(mention.lower()) if text else -1
        offsets = [pos if pos >= 0 else 0]
        out.append({"mention": mention, "category": category, "offsets": offsets})
    return out


def _index_named_entities(
    es: str,
    project: str,
    docker: str | None,
    document_id: str,
    entities: list[dict],
    dry_run: bool,
) -> int:
    n = 0
    for ent in entities:
        mention = ent["mention"]
        mention_norm = _mention_norm(mention)
        offsets = ent["offsets"]
        ne_id = _named_entity_id(document_id, offsets, mention_norm)
        body = {
            "type": "NamedEntity",
            "join": {"name": "NamedEntity", "parent": document_id},
            "category": ent["category"],
            "mention": mention,
            "mentionNorm": mention_norm,
            "mentionNormTextLength": len(mention_norm),
            "offsets": offsets,
            "extractor": _EXTRACTOR,
            "extractorLanguage": "ENGLISH",
            "isHidden": False,
            "documentId": document_id,
            "rootDocument": document_id,
            "parentDocument": document_id,
            "metadata": {"marshaloss_synced": True},
        }
        if dry_run:
            n += 1
            continue
        # routing must be the parent document id for join children
        _es_request(
            es,
            "PUT",
            f"/{project}/_doc/{ne_id}?routing={document_id}&refresh=false",
            body,
            docker,
        )
        n += 1
    return n


def _rebuild_structured_entities(
    es: str,
    project: str,
    docker: str | None,
    dry_run: bool,
) -> int:
    """Roll NamedEntity mentions into ``{project}.entities`` StructuredEntity docs."""
    entities_index = f"{project}.entities"
    # Datashare creates this on project create; skip create if already present.
    try:
        _es_request(es, "GET", f"/{entities_index}", None, docker)
    except RuntimeError as e:
        if "404" not in str(e):
            raise
        print(
            f"warning: {entities_index} missing — create the Datashare project first; "
            "skipping StructuredEntity rebuild",
            file=sys.stderr,
        )
        return 0

    # Aggregate by category + mentionNorm; collect document ids via composite-ish terms
    res = _es_request(
        es,
        "POST",
        f"/{project}/_search",
        {
            "size": 0,
            "query": {"term": {"type": "NamedEntity"}},
            "aggs": {
                "by_cat": {
                    "terms": {"field": "category", "size": 20},
                    "aggs": {
                        "by_mention": {
                            "terms": {"field": "mentionNorm", "size": 5000},
                            "aggs": {
                                "docs": {"terms": {"field": "documentId", "size": 500}},
                                "sample": {
                                    "top_hits": {
                                        "size": 1,
                                        "_source": ["mention", "category"],
                                    }
                                },
                            },
                        }
                    },
                }
            },
        },
        docker,
    )

    if not dry_run:
        # Clear previous marshaloss StructuredEntities (keep any FtM rows from ENTITIES stage)
        _es_request(
            es,
            "POST",
            f"/{entities_index}/_delete_by_query?refresh=true",
            {"query": {"term": {"model": "marshaloss"}}},
            docker,
        )

    written = 0
    for cat_bucket in (res.get("aggregations") or {}).get("by_cat", {}).get("buckets", []):
        category = cat_bucket["key"]
        entity_type = _ENTITY_TYPE.get(category, "Thing")
        for ment in cat_bucket.get("by_mention", {}).get("buckets", []):
            mention_norm = ment["key"]
            hits = (
                ment.get("sample", {})
                .get("hits", {})
                .get("hits", [])
            )
            mention = mention_norm
            if hits:
                mention = (hits[0].get("_source") or {}).get("mention") or mention_norm
            doc_ids = [b["key"] for b in ment.get("docs", {}).get("buckets", [])]
            entity_id = hashlib.sha256(f"{category}|{mention_norm}".encode()).hexdigest()[:32]
            _id = f"marshaloss_{entity_id}"
            body = {
                "type": "StructuredEntity",
                "entityId": entity_id,
                "model": "marshaloss",
                "entityType": entity_type,
                "modelVersions": ["1"],
                "documentIds": doc_ids,
                "properties": {
                    "marshaloss_name": [mention],
                    "marshaloss_category": [category],
                    "marshaloss_mentionNorm": [mention_norm],
                },
            }
            if dry_run:
                written += 1
                continue
            _es_request(
                es,
                "PUT",
                f"/{entities_index}/_doc/{_id}?refresh=false",
                body,
                docker,
            )
            written += 1

    if not dry_run and written:
        _es_request(es, "POST", f"/{entities_index}/_refresh", None, docker)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True, help="MarshalOSS case slug")
    ap.add_argument("--project", required=True, help="Datashare project / ES index name")
    ap.add_argument("--db", default=None, help="Path to marshal_oss.db")
    ap.add_argument("--es", default="http://127.0.0.1:9200")
    ap.add_argument("--es-via-docker", default=None, help="ES container name if port not published")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dbp = _db_path(args.db)
    if not dbp.is_file():
        print(f"DB not found: {dbp}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(dbp))
    conn.row_factory = sqlite3.Row
    case = conn.execute("SELECT id, files_root FROM cases WHERE slug=?", (args.slug,)).fetchone()
    if not case:
        print(f"case slug not found: {args.slug}", file=sys.stderr)
        return 1

    by_rel: dict[str, dict] = {}
    for f in conn.execute(
        "SELECT id, rel_path, name, meta FROM files WHERE case_id=? AND status='ready'",
        (case["id"],),
    ):
        meta = {}
        if f["meta"]:
            try:
                meta = json.loads(f["meta"])
            except Exception:
                meta = {}
        rows = conn.execute(
            "SELECT text FROM chunks WHERE file_id=? ORDER BY chunk_index",
            (f["id"],),
        ).fetchall()
        text = "\n\n".join((r["text"] or "").strip() for r in rows if (r["text"] or "").strip())
        by_rel[f["rel_path"].replace("\\", "/")] = {
            "name": f["name"],
            "text": text,
            "tags": _tags_from_meta(meta),
            "entities": _entities_from_meta(meta, text),
            "meta": meta,
        }

    docs = _scroll_docs(args.es, args.project, args.es_via_docker)
    matched = updated = skipped_empty = unmatched = 0
    entities_written = 0
    lower_map = {k.lower(): v for k, v in by_rel.items()}

    for hit in docs:
        src = hit.get("_source") or {}
        path = src.get("path") or ""
        rel = _rel_from_ds_path(path, args.project)
        rec = by_rel.get(rel) or lower_map.get(rel.lower())
        if rec is None:
            unmatched += 1
            continue
        matched += 1
        text = rec["text"]
        if not text and not rec["entities"]:
            skipped_empty += 1
            continue

        tags = list(dict.fromkeys((src.get("tags") or []) + rec["tags"]))
        categories = sorted({e["category"] for e in rec["entities"]})
        # Document.nerTags is Set<Pipeline.Type> (which NLP ran), NOT entity categories.
        ner_tags = ["CORENLP"] if rec["entities"] else list(src.get("nerTags") or [])
        source = rec["meta"].get("source") if isinstance(rec["meta"], dict) else None

        if args.dry_run:
            print(
                f"WOULD update {rel} chars={len(text)} tags={len(tags)} "
                f"entities={len(rec['entities'])} categories={categories}"
            )
            updated += 1
            entities_written += len(rec["entities"])
            continue

        body = {
            "script": {
                "lang": "painless",
                "source": (
                    "ctx._source.content = params.content;"
                    "ctx._source.contentTextLength = params.len;"
                    "ctx._source.tags = params.tags;"
                    "ctx._source.nerTags = params.nerTags;"
                    "if (ctx._source.metadata == null) {ctx._source.metadata = new HashMap();}"
                    "ctx._source.metadata.marshaloss_synced = true;"
                    "ctx._source.metadata.marshaloss_slug = params.slug;"
                    "if (params.source != null) {ctx._source.metadata.marshaloss_source = params.source;}"
                ),
                "params": {
                    "content": text,
                    "len": len(text),
                    "tags": tags,
                    "nerTags": ner_tags,
                    "slug": args.slug,
                    "source": source,
                },
            }
        }
        _es_request(
            args.es, "POST",
            f"/{args.project}/_update/{hit['_id']}?refresh=false",
            body, args.es_via_docker,
        )
        entities_written += _index_named_entities(
            args.es, args.project, args.es_via_docker,
            hit["_id"], rec["entities"], dry_run=False,
        )
        updated += 1
        if updated % 25 == 0:
            print(f"… {updated} docs, {entities_written} entities")

    if not args.dry_run and updated:
        _es_request(args.es, "POST", f"/{args.project}/_refresh", None, args.es_via_docker)

    structured_written = _rebuild_structured_entities(
        args.es, args.project, args.es_via_docker, args.dry_run,
    )

    print(
        json.dumps(
            {
                "project": args.project,
                "slug": args.slug,
                "datashare_docs": len(docs),
                "marshal_ready_files": len(by_rel),
                "matched": matched,
                "updated": updated,
                "named_entities_written": entities_written,
                "structured_entities_written": structured_written,
                "matched_but_no_text": skipped_empty,
                "unmatched_datashare_only": unmatched,
                "dry_run": args.dry_run,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
