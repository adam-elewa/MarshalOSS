#!/usr/bin/env python3
"""Push MarshalOSS extracted text + tags/entities into a Datashare project index.

Datashare SCAN+INDEX often leaves scanned PDFs and media with empty content when
OCR is off. This fills ``content`` and ``tags`` from MarshalOSS so Datashare
search/browse reflects the defense pipeline.

Usage (on the host that runs Datashare + has the MarshalOSS DB):

  python3 review/scripts/sync-from-marshaloss.py \\
    --slug david-jones \\
    --project david-jones \\
    --es http://127.0.0.1:9200

Or via docker ES:

  python3 review/scripts/sync-from-marshaloss.py \\
    --slug david-jones --project david-jones \\
    --es-via-docker datashare-elasticsearch-1
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


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
            "docker",
            "exec",
            "-i",
            docker,
            "curl",
            "-s",
            "-X",
            method,
            f"http://localhost:9200{url_path}",
            "-H",
            "Content-Type: application/json",
        ]
        if payload is not None:
            cmd += ["--data-binary", "@-"]
            out = subprocess.check_output(cmd, input=payload.decode("utf-8"), text=True)
        else:
            out = subprocess.check_output(cmd, text=True)
        return json.loads(out) if out.strip() else {}

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
        "_source": ["path", "contentTextLength", "tags", "title", "contentType"],
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
            es,
            "POST",
            "/_search/scroll",
            {"scroll": "2m", "scroll_id": scroll_id},
            docker,
        )
        scroll_id = res.get("_scroll_id")
    if scroll_id:
        try:
            _es_request(es, "DELETE", "/_search/scroll", {"scroll_id": [scroll_id]}, docker)
        except Exception:
            pass
    return docs


def _rel_from_ds_path(path: str, project: str) -> str:
    # /home/datashare/data/<project>/rel → rel
    marker = f"/data/{project}/"
    if marker in path:
        return path.split(marker, 1)[1]
    # fallback: strip up to project name segment
    parts = path.split("/")
    if project in parts:
        i = parts.index(project)
        return "/".join(parts[i + 1 :])
    return path.lstrip("/")


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
            "meta": meta,
        }

    docs = _scroll_docs(args.es, args.project, args.es_via_docker)
    matched = updated = skipped_empty = unmatched = 0
    for hit in docs:
        src = hit.get("_source") or {}
        path = src.get("path") or ""
        rel = _rel_from_ds_path(path, args.project)
        # try exact, then case-insensitive
        rec = by_rel.get(rel)
        if rec is None:
            lower = {k.lower(): v for k, v in by_rel.items()}
            rec = lower.get(rel.lower())
        if rec is None:
            unmatched += 1
            continue
        matched += 1
        text = rec["text"]
        if not text:
            skipped_empty += 1
            continue
        tags = list(dict.fromkeys((src.get("tags") or []) + rec["tags"]))
        source = rec["meta"].get("source") if isinstance(rec["meta"], dict) else None
        body = {
            "script": {
                "lang": "painless",
                "source": (
                    "ctx._source.content = params.content;"
                    "ctx._source.contentTextLength = params.len;"
                    "ctx._source.tags = params.tags;"
                    "if (ctx._source.metadata == null) {ctx._source.metadata = new HashMap();}"
                    "ctx._source.metadata.marshaloss_synced = true;"
                    "ctx._source.metadata.marshaloss_slug = params.slug;"
                    "if (params.source != null) {ctx._source.metadata.marshaloss_source = params.source;}"
                ),
                "params": {
                    "content": text,
                    "len": len(text),
                    "tags": tags,
                    "slug": args.slug,
                    "source": source,
                },
            }
        }
        if args.dry_run:
            print(f"WOULD update {rel} chars={len(text)} tags={len(tags)}")
            updated += 1
            continue
        _es_request(
            args.es,
            "POST",
            f"/{args.project}/_update/{hit['_id']}?refresh=false",
            body,
            args.es_via_docker,
        )
        updated += 1
        if updated % 25 == 0:
            print(f"… {updated} updated")

    if not args.dry_run and updated:
        _es_request(args.es, "POST", f"/{args.project}/_refresh", None, args.es_via_docker)

    print(
        json.dumps(
            {
                "project": args.project,
                "slug": args.slug,
                "datashare_docs": len(docs),
                "marshal_ready_files": len(by_rel),
                "matched": matched,
                "updated": updated,
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
