from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# FTS5 MATCH treats these as operators / syntax; natural-language asks often include them.
_FTS5_SPECIAL = re.compile(r"[^\w\s']+", re.UNICODE)
_FTS5_OPS = frozenset({"AND", "OR", "NOT", "NEAR"})
# Drop question/filler words so MATCH (implicit AND) still hits discovery text.
_FTS5_STOP = frozenset(
    {
        "a", "an", "the", "and", "or", "not", "of", "to", "in", "on", "at", "for",
        "from", "by", "with", "as", "is", "are", "was", "were", "be", "been", "being",
        "do", "does", "did", "what", "which", "who", "whom", "whose", "where", "when",
        "why", "how", "can", "could", "would", "should", "may", "might", "will",
        "this", "that", "these", "those", "it", "its", "he", "she", "they", "them",
        "his", "her", "their", "our", "your", "my", "we", "you", "i", "me", "us",
        "about", "into", "over", "under", "than", "then", "there", "here", "also",
        "just", "please", "tell", "show", "find", "give", "list", "summarize",
        "summary", "describe", "explain", "any", "all", "some", "have", "has", "had",
    }
)


def sanitize_fts5_query(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH query.

    Uses OR of content tokens so natural-language asks (with punctuation and
    filler words) still retrieve; bm25 ranks stronger overlaps higher.
    """
    q = (query or "").replace('"', " ").replace("'", " ")
    q = _FTS5_SPECIAL.sub(" ", q)
    tokens = []
    seen: set[str] = set()
    for t in q.split():
        if not t or t.upper() in _FTS5_OPS:
            continue
        if t.lower() in _FTS5_STOP:
            continue
        if len(t) == 1 and not t.isdigit():
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        tokens.append(t)
    # If everything was stopwords, fall back to raw alphanumerics so MATCH isn't empty.
    if not tokens:
        tokens = [t for t in q.split() if t and t.upper() not in _FTS5_OPS]
    if not tokens:
        return ""
    if len(tokens) == 1:
        return tokens[0]
    return " OR ".join(tokens)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Case:
    id: int
    slug: str
    display_name: str
    files_root: str
    status: str


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              slug TEXT NOT NULL UNIQUE,
              display_name TEXT NOT NULL,
              files_root TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
              rel_path TEXT NOT NULL,
              name TEXT NOT NULL,
              bates TEXT,
              mime TEXT,
              size INTEGER NOT NULL DEFAULT 0,
              mtime REAL,
              sha256 TEXT,
              status TEXT NOT NULL DEFAULT 'pending',
              error TEXT,
              ingested_at TEXT,
              UNIQUE(case_id, rel_path)
            );
            CREATE TABLE IF NOT EXISTS chunks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
              chunk_index INTEGER NOT NULL,
              text TEXT NOT NULL,
              page_start INTEGER,
              page_end INTEGER,
              heading TEXT,
              bates TEXT,
              start_sec REAL,
              end_sec REAL,
              UNIQUE(file_id, chunk_index)
            );
            CREATE TABLE IF NOT EXISTS media_segments (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
              case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
              seg_index INTEGER NOT NULL,
              kind TEXT NOT NULL,
              start_sec REAL NOT NULL,
              end_sec REAL NOT NULL,
              text TEXT NOT NULL,
              speaker TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_media_segments_file
              ON media_segments(file_id, start_sec);
            CREATE TABLE IF NOT EXISTS embeddings (
              chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
              model TEXT NOT NULL,
              dim INTEGER NOT NULL,
              vector BLOB NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
              text,
              bates,
              rel_path,
              content='',
              tokenize='porter'
            );
            CREATE TABLE IF NOT EXISTS production_manifest (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
              rel_path TEXT NOT NULL,
              name TEXT NOT NULL,
              ext TEXT,
              detected_ext TEXT,
              size INTEGER NOT NULL DEFAULT 0,
              mtime REAL,
              sha256 TEXT NOT NULL,
              mime TEXT,
              bates TEXT,
              kind TEXT NOT NULL,
              ingestible INTEGER NOT NULL DEFAULT 0,
              parent_zip TEXT,
              produced_from TEXT NOT NULL DEFAULT 'native',
              ingest_status TEXT NOT NULL DEFAULT 'pending',
              quality_score REAL,
              quality_reasons TEXT,
              chunk_count INTEGER NOT NULL DEFAULT 0,
              file_id INTEGER,
              escalated INTEGER NOT NULL DEFAULT 0,
              source TEXT,
              error TEXT,
              entities_json TEXT,
              tags_json TEXT,
              UNIQUE(case_id, rel_path)
            );
            CREATE INDEX IF NOT EXISTS idx_manifest_case_kind
              ON production_manifest(case_id, kind);
            CREATE INDEX IF NOT EXISTS idx_manifest_case_sha
              ON production_manifest(case_id, sha256);
            CREATE INDEX IF NOT EXISTS idx_manifest_case_status
              ON production_manifest(case_id, ingest_status);
            """
        )
        # Additive column for quality / AI-OCR audit JSON
        cols = {
            r[1]
            for r in self.conn.execute("PRAGMA table_info(files)").fetchall()
        }
        if "meta" not in cols:
            self.conn.execute("ALTER TABLE files ADD COLUMN meta TEXT")
        ccols = {
            r[1]
            for r in self.conn.execute("PRAGMA table_info(chunks)").fetchall()
        }
        for col in ("start_sec", "end_sec"):
            if col not in ccols:
                self.conn.execute(f"ALTER TABLE chunks ADD COLUMN {col} REAL")
        # Additive production_manifest ingest/quality columns (older DBs)
        mcols = {
            r[1]
            for r in self.conn.execute("PRAGMA table_info(production_manifest)").fetchall()
        }
        for col, decl in [
            ("ingest_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("quality_score", "REAL"),
            ("quality_reasons", "TEXT"),
            ("chunk_count", "INTEGER NOT NULL DEFAULT 0"),
            ("file_id", "INTEGER"),
            ("escalated", "INTEGER NOT NULL DEFAULT 0"),
            ("source", "TEXT"),
            ("error", "TEXT"),
            ("entities_json", "TEXT"),
            ("tags_json", "TEXT"),
        ]:
            if mcols and col not in mcols:
                self.conn.execute(f"ALTER TABLE production_manifest ADD COLUMN {col} {decl}")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notebook_pages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
              path TEXT NOT NULL,
              title TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(case_id, path)
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notebook_pages_case ON notebook_pages(case_id, path)"
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- settings / cases ---
    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_setting(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def upsert_case(self, slug: str, display_name: str, files_root: str) -> Case:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO cases(slug, display_name, files_root, status, created_at, updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(slug) DO UPDATE SET
              display_name=excluded.display_name,
              files_root=excluded.files_root,
              updated_at=excluded.updated_at
            """,
            (slug, display_name, files_root, "active", now, now),
        )
        self.conn.commit()
        return self.get_case_by_slug(slug)  # type: ignore

    def list_cases(self) -> list[Case]:
        rows = self.conn.execute(
            "SELECT id, slug, display_name, files_root, status FROM cases WHERE status='active' ORDER BY display_name"
        ).fetchall()
        return [Case(**dict(r)) for r in rows]

    def get_case_by_id(self, case_id: int) -> Case | None:
        row = self.conn.execute(
            "SELECT id, slug, display_name, files_root, status FROM cases WHERE id=?",
            (case_id,),
        ).fetchone()
        return Case(**dict(row)) if row else None

    def get_case_by_slug(self, slug: str) -> Case | None:
        row = self.conn.execute(
            "SELECT id, slug, display_name, files_root, status FROM cases WHERE slug=?",
            (slug,),
        ).fetchone()
        return Case(**dict(row)) if row else None

    def set_active_case(self, case_id: int) -> Case:
        c = self.get_case_by_id(case_id)
        if not c:
            raise ValueError("case not found")
        self.set_setting("active_case_id", str(case_id))
        return c

    def update_case_display_name(self, case_id: int, display_name: str) -> Case:
        name = (display_name or "").strip()
        if not name:
            raise ValueError("display_name is required")
        c = self.get_case_by_id(case_id)
        if not c:
            raise ValueError("case not found")
        now = utc_now()
        self.conn.execute(
            "UPDATE cases SET display_name=?, updated_at=? WHERE id=?",
            (name, now, case_id),
        )
        self.conn.commit()
        updated = self.get_case_by_id(case_id)
        if not updated:
            raise ValueError("case not found")
        return updated

    def get_active_case(self) -> Case | None:
        raw = self.get_setting("active_case_id")
        if not raw:
            cases = self.list_cases()
            return cases[0] if cases else None
        return self.get_case_by_id(int(raw))

    # --- files / chunks ---
    def upsert_file(
        self,
        case_id: int,
        rel_path: str,
        name: str,
        bates: str | None,
        mime: str | None,
        size: int,
        mtime: float | None,
        sha256: str | None,
        status: str = "pending",
        error: str | None = None,
        meta: dict[str, Any] | str | None = None,
    ) -> int:
        now = utc_now() if status == "ready" else None
        meta_s = (
            json.dumps(meta, ensure_ascii=False)
            if isinstance(meta, dict)
            else meta
        )
        self.conn.execute(
            """
            INSERT INTO files(case_id, rel_path, name, bates, mime, size, mtime, sha256, status, error, ingested_at, meta)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(case_id, rel_path) DO UPDATE SET
              name=excluded.name,
              bates=excluded.bates,
              mime=excluded.mime,
              size=excluded.size,
              mtime=excluded.mtime,
              sha256=excluded.sha256,
              status=excluded.status,
              error=excluded.error,
              meta=COALESCE(excluded.meta, files.meta),
              ingested_at=COALESCE(excluded.ingested_at, files.ingested_at)
            """,
            (case_id, rel_path, name, bates, mime, size, mtime, sha256, status, error, now, meta_s),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM files WHERE case_id=? AND rel_path=?",
            (case_id, rel_path),
        ).fetchone()
        return int(row["id"])

    def get_file(self, case_id: int, rel_path: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM files WHERE case_id=? AND rel_path=?",
            (case_id, rel_path),
        ).fetchone()

    def get_file_detail(self, case_id: int, rel_path: str) -> dict[str, Any] | None:
        """File row + parsed meta + extracted text chunks + manifest entities/tags."""
        row = self.get_file(case_id, rel_path)
        if not row:
            return None
        d = dict(row)
        meta: dict[str, Any] = {}
        raw_meta = d.get("meta")
        if raw_meta:
            try:
                loaded = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                if isinstance(loaded, dict):
                    meta = loaded
            except Exception:
                meta = {}
        d["meta"] = meta
        chunks = self.conn.execute(
            """
            SELECT id, chunk_index, text, page_start, page_end, heading, start_sec, end_sec
            FROM chunks WHERE file_id=? ORDER BY chunk_index
            """,
            (d["id"],),
        ).fetchall()
        d["chunks"] = [dict(c) for c in chunks]
        d["text"] = "\n\n".join(
            (c["text"] or "").strip() for c in d["chunks"] if (c.get("text") or "").strip()
        )
        entities = meta.get("entities") or []
        tags = meta.get("tags") or []
        man = self.conn.execute(
            """
            SELECT entities_json, tags_json, ingest_status, quality_score, quality_reasons,
                   kind, chunk_count, source, error
            FROM production_manifest WHERE case_id=? AND rel_path=?
            """,
            (case_id, rel_path),
        ).fetchone()
        if man:
            md = dict(man)
            if md.get("entities_json") and not entities:
                try:
                    entities = json.loads(md["entities_json"])
                except Exception:
                    pass
            if md.get("tags_json") and not tags:
                try:
                    parsed_tags = json.loads(md["tags_json"])
                    tags = parsed_tags if isinstance(parsed_tags, list) else tags
                except Exception:
                    pass
            d["manifest"] = {
                "ingest_status": md.get("ingest_status"),
                "quality_score": md.get("quality_score"),
                "quality_reasons": md.get("quality_reasons"),
                "kind": md.get("kind"),
                "chunk_count": md.get("chunk_count"),
                "source": md.get("source"),
                "error": md.get("error"),
            }
        else:
            d["manifest"] = None
        d["entities"] = entities if isinstance(entities, list) else []
        d["tags"] = tags if isinstance(tags, list) else []
        # User annotations (editable via MCP/API) — separate from ingest tags
        d["user_note"] = meta.get("user_note") if isinstance(meta.get("user_note"), str) else (
            str(meta["user_note"]) if meta.get("user_note") is not None else ""
        )
        ut = meta.get("user_tags")
        d["user_tags"] = [str(t) for t in ut] if isinstance(ut, list) else []
        return d

    def _load_file_meta(self, case_id: int, rel_path: str) -> tuple[int, dict[str, Any]] | None:
        row = self.get_file(case_id, rel_path)
        if not row:
            return None
        meta: dict[str, Any] = {}
        raw = row["meta"]
        if raw:
            try:
                loaded = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(loaded, dict):
                    meta = loaded
            except Exception:
                meta = {}
        return int(row["id"]), meta

    def set_file_user_note(self, case_id: int, rel_path: str, note: str) -> dict[str, Any]:
        """Set reviewer note on a file (does not modify discovery bytes on disk)."""
        loaded = self._load_file_meta(case_id, rel_path)
        if not loaded:
            raise ValueError(f"file not found: {rel_path}")
        file_id, meta = loaded
        meta["user_note"] = note
        self.conn.execute(
            "UPDATE files SET meta=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), file_id),
        )
        self.conn.commit()
        return {"rel_path": rel_path, "user_note": note}

    def set_file_user_tags(self, case_id: int, rel_path: str, tags: list[str]) -> dict[str, Any]:
        """Set reviewer tags on a file (does not modify discovery bytes on disk)."""
        loaded = self._load_file_meta(case_id, rel_path)
        if not loaded:
            raise ValueError(f"file not found: {rel_path}")
        file_id, meta = loaded
        clean = [str(t).strip() for t in tags if str(t).strip()]
        meta["user_tags"] = clean
        self.conn.execute(
            "UPDATE files SET meta=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), file_id),
        )
        self.conn.commit()
        return {"rel_path": rel_path, "user_tags": clean}

    def list_files(self, case_id: int, prefix: str = "") -> list[dict[str, Any]]:
        if prefix:
            rows = self.conn.execute(
                """
                SELECT id, rel_path, name, bates, mime, size, status
                FROM files WHERE case_id=? AND rel_path LIKE ?
                ORDER BY rel_path
                """,
                (case_id, prefix.rstrip("/") + "/%"),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT id, rel_path, name, bates, mime, size, status
                FROM files WHERE case_id=?
                ORDER BY rel_path
                """,
                (case_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def replace_production_manifest(self, case_id: int, records: Iterable[Any]) -> int:
        """Replace all production_manifest rows for a case (full inventory refresh)."""
        self.conn.execute("DELETE FROM production_manifest WHERE case_id=?", (case_id,))
        rows = []
        for r in records:
            # dataclass or dict
            if hasattr(r, "__dataclass_fields__"):
                d = {k: getattr(r, k) for k in r.__dataclass_fields__}
            else:
                d = dict(r)
            ingestible = 1 if d.get("ingestible") else 0
            status = "pending" if ingestible else "skipped_not_covered"
            rows.append(
                (
                    case_id,
                    d["rel_path"],
                    d["name"],
                    d.get("ext"),
                    d.get("detected_ext"),
                    int(d.get("size") or 0),
                    d.get("mtime"),
                    d["sha256"],
                    d.get("mime"),
                    d.get("bates"),
                    d["kind"],
                    ingestible,
                    d.get("parent_zip"),
                    d.get("produced_from") or "native",
                    status,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO production_manifest(
              case_id, rel_path, name, ext, detected_ext, size, mtime, sha256,
              mime, bates, kind, ingestible, parent_zip, produced_from, ingest_status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def update_manifest_ingest(
        self,
        case_id: int,
        rel_path: str,
        *,
        ingest_status: str,
        quality_score: float | None = None,
        quality_reasons: list[str] | None = None,
        chunk_count: int = 0,
        file_id: int | None = None,
        escalated: bool = False,
        source: str | None = None,
        error: str | None = None,
        entities: list[Any] | dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Update one manifest row after ingest / entity pass."""
        self.conn.execute(
            """
            UPDATE production_manifest SET
              ingest_status=?,
              quality_score=COALESCE(?, quality_score),
              quality_reasons=COALESCE(?, quality_reasons),
              chunk_count=?,
              file_id=COALESCE(?, file_id),
              escalated=?,
              source=COALESCE(?, source),
              error=?,
              entities_json=COALESCE(?, entities_json),
              tags_json=COALESCE(?, tags_json)
            WHERE case_id=? AND rel_path=?
            """,
            (
                ingest_status,
                quality_score,
                json.dumps(quality_reasons, ensure_ascii=False) if quality_reasons is not None else None,
                chunk_count,
                file_id,
                1 if escalated else 0,
                source,
                error,
                json.dumps(entities, ensure_ascii=False) if entities is not None else None,
                json.dumps(tags, ensure_ascii=False) if tags is not None else None,
                case_id,
                rel_path,
            ),
        )
        self.conn.commit()

    def list_production_manifest(
        self,
        case_id: int,
        *,
        kind: str | None = None,
        ingestible: bool | None = None,
        ingest_status: str | None = None,
        prefix: str = "",
        limit: int = 5000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["case_id=?"]
        args: list[Any] = [case_id]
        if kind:
            clauses.append("kind=?")
            args.append(kind)
        if ingestible is not None:
            clauses.append("ingestible=?")
            args.append(1 if ingestible else 0)
        if ingest_status:
            clauses.append("ingest_status=?")
            args.append(ingest_status)
        if prefix:
            clauses.append("rel_path LIKE ?")
            args.append(prefix.rstrip("/") + "/%")
        args.extend([limit, offset])
        sql = f"""
            SELECT * FROM production_manifest
            WHERE {' AND '.join(clauses)}
            ORDER BY rel_path
            LIMIT ? OFFSET ?
        """
        out = []
        for r in self.conn.execute(sql, args).fetchall():
            d = dict(r)

            def _loads(val: Any) -> Any:
                if isinstance(val, str) and val:
                    try:
                        return json.loads(val)
                    except Exception:
                        return val
                return val

            d["quality_reasons"] = _loads(d.get("quality_reasons"))
            d["entities"] = _loads(d.get("entities_json"))
            d["tags"] = _loads(d.get("tags_json"))
            out.append(d)
        return out

    def list_files_with_meta(self, case_id: int) -> list[dict[str, Any]]:
        """Ingested files with parsed meta (for category browse)."""
        rows = self.conn.execute(
            """
            SELECT id, rel_path, name, bates, mime, size, status, meta
            FROM files WHERE case_id=?
            ORDER BY rel_path
            """,
            (case_id,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            meta: dict[str, Any] = {}
            raw = d.pop("meta", None)
            if raw:
                try:
                    loaded = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(loaded, dict):
                        meta = loaded
                except Exception:
                    meta = {}
            d["meta"] = meta
            d["tags"] = meta.get("tags") or []
            d["entities"] = meta.get("entities") or []
            out.append(d)
        return out

    def find_files_by_facet(
        self,
        case_id: int,
        *,
        tag: str | None = None,
        entity: str | None = None,
        entity_type: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return ingested files matching a tag and/or entity (case-insensitive)."""
        if not tag and not entity and not entity_type:
            return []
        from .entities import person_match_key, person_names_compatible

        tag_l = (tag or "").strip().lower()
        ent_raw = (entity or "").strip()
        ent_l = ent_raw.lower()
        type_l = (entity_type or "").strip().lower()
        # Fuzzy person matching when browsing people/officers (middle-initial variants).
        use_person_fuzzy = type_l in ("person", "officer") or (
            not type_l and len(person_match_key(ent_raw) or ()) >= 2
        )
        out: list[dict[str, Any]] = []
        rows = self.conn.execute(
            """
            SELECT id, rel_path, name, bates, mime, size, status, meta
            FROM files WHERE case_id=?
            ORDER BY rel_path
            """,
            (case_id,),
        ).fetchall()
        for row in rows:
            d = dict(row)
            meta: dict[str, Any] = {}
            raw = d.pop("meta", None)
            if raw:
                try:
                    loaded = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(loaded, dict):
                        meta = loaded
                except Exception:
                    meta = {}
            ents = meta.get("entities") or []
            tags = meta.get("tags") or []
            matched = False
            if tag_l:
                for t in tags:
                    if isinstance(t, str):
                        label = t
                    elif isinstance(t, dict):
                        label = str(t.get("name") or t.get("label") or t.get("tag") or "")
                    else:
                        label = str(t)
                    if label.strip().lower() == tag_l:
                        matched = True
                        break
                if not matched:
                    continue
            if ent_l or type_l:
                ent_hit = False
                for e in ents:
                    if isinstance(e, dict):
                        et = str(e.get("type") or "other").strip().lower()
                        label = str(
                            e.get("canonical")
                            or e.get("text")
                            or e.get("value")
                            or e.get("name")
                            or ""
                        ).strip()
                        aliases = e.get("aliases") or []
                    else:
                        et, label, aliases = "other", str(e).strip(), []
                    if type_l:
                        if type_l in ("person", "officer"):
                            if et not in ("person", "officer"):
                                continue
                        elif et != type_l:
                            continue
                    names = [label] + [str(a) for a in aliases if a]
                    if ent_l:
                        exact = any(n.strip().lower() == ent_l for n in names if n)
                        fuzzy = use_person_fuzzy and any(
                            person_names_compatible(ent_raw, n) for n in names if n
                        )
                        if exact or fuzzy:
                            ent_hit = True
                            break
                    else:
                        ent_hit = True
                        break
                if not ent_hit:
                    continue
                matched = True
            if not matched and not tag_l:
                continue
            out.append(
                {
                    "id": d["id"],
                    "rel_path": d["rel_path"],
                    "name": d["name"],
                    "bates": d.get("bates"),
                    "mime": d.get("mime"),
                    "size": d.get("size"),
                    "status": d.get("status"),
                    "type": "file",
                }
            )
            if len(out) >= limit:
                break
        return out

    def facet_doc_counts_for_labels(
        self,
        case_id: int,
        *,
        entities: list[tuple[str, str]] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Count ingested docs matching each entity/tag label (one meta scan).

        Entity keys are (type, label). Person/officer labels use the same fuzzy
        name matching as find_files_by_facet so counts match the facet list.
        """
        from collections import Counter

        from .entities import person_match_key, person_names_compatible

        want_ents = [
            (str(et or "other").strip().lower() or "other", str(lab or "").strip())
            for et, lab in (entities or [])
            if str(lab or "").strip()
        ]
        want_tags = [str(t or "").strip() for t in (tags or []) if str(t or "").strip()]
        if not want_ents and not want_tags:
            return {"entities": {}, "tags": {}}

        ent_counts: Counter[tuple[str, str]] = Counter()
        tag_counts: Counter[str] = Counter()
        tag_l_map = {t.lower(): t for t in want_tags}

        rows = self.conn.execute(
            "SELECT meta FROM files WHERE case_id=?",
            (case_id,),
        ).fetchall()
        for row in rows:
            raw = row["meta"] if isinstance(row, sqlite3.Row) else row[0]
            if not raw:
                continue
            try:
                meta = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                continue
            if not isinstance(meta, dict):
                continue
            ents = meta.get("entities") or []
            file_tags = meta.get("tags") or []

            if want_tags and file_tags:
                seen_tag: set[str] = set()
                for t in file_tags:
                    if isinstance(t, str):
                        label = t
                    elif isinstance(t, dict):
                        label = str(t.get("name") or t.get("label") or t.get("tag") or "")
                    else:
                        label = str(t)
                    key = label.strip().lower()
                    orig = tag_l_map.get(key)
                    if orig and orig not in seen_tag:
                        seen_tag.add(orig)
                        tag_counts[orig] += 1

            if want_ents and ents:
                file_ents: list[tuple[str, str, list[str]]] = []
                for e in ents:
                    if isinstance(e, dict):
                        et = str(e.get("type") or "other").strip().lower() or "other"
                        label = str(
                            e.get("canonical")
                            or e.get("text")
                            or e.get("value")
                            or e.get("name")
                            or ""
                        ).strip()
                        aliases = [str(a) for a in (e.get("aliases") or []) if a]
                    else:
                        et, label, aliases = "other", str(e).strip(), []
                    if label:
                        file_ents.append((et, label, aliases))
                hit: set[tuple[str, str]] = set()
                for et, lab in want_ents:
                    if (et, lab) in hit:
                        continue
                    use_fuzzy = et in ("person", "officer") or (
                        len(person_match_key(lab) or ()) >= 2
                    )
                    lab_l = lab.lower()
                    for fet, flabel, aliases in file_ents:
                        if et in ("person", "officer"):
                            if fet not in ("person", "officer"):
                                continue
                        elif fet != et:
                            continue
                        names = [flabel] + aliases
                        exact = any(n.strip().lower() == lab_l for n in names if n)
                        fuzzy = use_fuzzy and any(
                            person_names_compatible(lab, n) for n in names if n
                        )
                        if exact or fuzzy:
                            hit.add((et, lab))
                            ent_counts[(et, lab)] += 1
                            break

        return {
            "entities": {f"{et}::{lab}": n for (et, lab), n in ent_counts.items()},
            "tags": dict(tag_counts),
        }

    def case_entity_tag_stats(self, case_id: int, top_n: int = 25) -> dict[str, Any]:
        """Aggregate entities/tags across ingested files for case overview."""
        from collections import Counter

        from .entities import (
            collapse_person_counts,
            filter_tags,
            is_noise_entity,
            normalize_tag,
        )

        type_counts: Counter[str] = Counter()
        entity_counts: Counter[tuple[str, str]] = Counter()
        tag_counts: Counter[str] = Counter()
        files_with_entities = 0
        files_with_tags = 0
        entity_mentions = 0

        rows = self.conn.execute(
            "SELECT meta FROM files WHERE case_id=?",
            (case_id,),
        ).fetchall()
        for row in rows:
            raw = row["meta"] if isinstance(row, sqlite3.Row) else row[0]
            if not raw:
                continue
            try:
                meta = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                continue
            if not isinstance(meta, dict):
                continue
            ents = meta.get("entities") or []
            tags = meta.get("tags") or []
            kept_ents = False
            for e in ents:
                if isinstance(e, dict):
                    et = str(e.get("type") or "other").strip().lower() or "other"
                    label = str(
                        e.get("canonical") or e.get("text") or e.get("value") or e.get("name") or ""
                    ).strip()
                else:
                    et, label = "other", str(e).strip()
                # Substantive people/orgs/places only — drop date/time/bates/other noise.
                if is_noise_entity(et, label):
                    continue
                kept_ents = True
                entity_mentions += 1
                type_counts[et] += 1
                if label:
                    entity_counts[(et, label)] += 1
            if kept_ents:
                files_with_entities += 1
            cleaned_tags = filter_tags(tags, limit=50)  # normalize/deny; case-level cap via sort
            if cleaned_tags:
                files_with_tags += 1
            for label in cleaned_tags:
                tag_counts[normalize_tag(label)] += 1

        # Collapse David Jones / David E. Jones / David Eli Jones etc.
        entity_counts = Counter(collapse_person_counts(entity_counts))
        # Drop one-off locations (incidental places) and one-off tags from overview.
        entity_counts = Counter(
            {
                k: n
                for k, n in entity_counts.items()
                if not (k[0] == "location" and n < 2)
            }
        )
        tag_counts = Counter({k: n for k, n in tag_counts.items() if n >= 2})
        # Rebuild type unique counts from collapsed map (person rows may have moved).
        type_counts = Counter()
        for (et, _label), n in entity_counts.items():
            type_counts[et] += n

        entities_by_type: dict[str, list[dict[str, Any]]] = {}
        unique_by_type: Counter[str] = Counter()
        for (et, label), n in entity_counts.most_common():
            unique_by_type[et] += 1
            bucket = entities_by_type.setdefault(et, [])
            # keep a generous per-type list for the overview drill-down
            if len(bucket) < max(top_n, 200):
                bucket.append({"type": et, "canonical": label, "count": n})

        return {
            "files_with_entities": files_with_entities,
            "files_with_tags": files_with_tags,
            "entity_mentions": entity_mentions,
            "unique_entities": len(entity_counts),
            "unique_tags": len(tag_counts),
            "by_type": [
                {
                    "type": t,
                    "count": unique_by_type[t],
                    "mentions": type_counts[t],
                }
                for t, _ in unique_by_type.most_common()
            ],
            "entities_by_type": entities_by_type,
            "top_entities": [
                {"type": t, "canonical": label, "count": n}
                for (t, label), n in entity_counts.most_common(top_n)
            ],
            "top_tags": [
                {"tag": tag, "count": n}
                for tag, n in sorted(
                    tag_counts.most_common(max(top_n, 100)),
                    key=lambda x: str(x[0]).lower(),
                )
            ],
        }

    def production_manifest_summary(self, case_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS file_count,
                   COALESCE(SUM(size),0) AS total_bytes,
                   SUM(CASE WHEN ingestible=1 THEN 1 ELSE 0 END) AS ingestible_count,
                   COALESCE(SUM(CASE WHEN ingestible=1 THEN size ELSE 0 END),0) AS ingestible_bytes,
                   COUNT(DISTINCT sha256) AS unique_sha256,
                   SUM(CASE WHEN ingest_status='ready' THEN 1 ELSE 0 END) AS ready_count,
                   SUM(CASE WHEN ingest_status='error' THEN 1 ELSE 0 END) AS error_count,
                   SUM(CASE WHEN ingest_status='pending' THEN 1 ELSE 0 END) AS pending_count,
                   SUM(CASE WHEN ingest_status='skipped' THEN 1 ELSE 0 END) AS skipped_count,
                   SUM(CASE WHEN ingest_status='skipped_not_covered' THEN 1 ELSE 0 END) AS not_covered_count,
                   SUM(CASE WHEN escalated=1 THEN 1 ELSE 0 END) AS escalated_count,
                   AVG(CASE WHEN quality_score IS NOT NULL THEN quality_score END) AS avg_quality,
                   SUM(CASE WHEN quality_score IS NOT NULL AND quality_score < 0.72 THEN 1 ELSE 0 END) AS low_quality_count,
                   SUM(CASE WHEN quality_score IS NOT NULL AND quality_score >= 0.85 THEN 1 ELSE 0 END) AS high_quality_count,
                   SUM(chunk_count) AS total_chunks
            FROM production_manifest WHERE case_id=?
            """,
            (case_id,),
        ).fetchone()
        by_kind = {
            r["kind"]: r["n"]
            for r in self.conn.execute(
                """
                SELECT kind, COUNT(*) AS n FROM production_manifest
                WHERE case_id=? GROUP BY kind ORDER BY n DESC
                """,
                (case_id,),
            )
        }
        by_ext = {
            r["ext"]: r["n"]
            for r in self.conn.execute(
                """
                SELECT ext, COUNT(*) AS n FROM production_manifest
                WHERE case_id=? GROUP BY ext ORDER BY n DESC LIMIT 40
                """,
                (case_id,),
            )
        }
        by_status = {
            r["ingest_status"]: r["n"]
            for r in self.conn.execute(
                """
                SELECT ingest_status, COUNT(*) AS n FROM production_manifest
                WHERE case_id=? GROUP BY ingest_status ORDER BY n DESC
                """,
                (case_id,),
            )
        }
        return {
            "file_count": int(row["file_count"]),
            "total_bytes": int(row["total_bytes"]),
            "ingestible_count": int(row["ingestible_count"] or 0),
            "ingestible_bytes": int(row["ingestible_bytes"] or 0),
            "unique_sha256": int(row["unique_sha256"] or 0),
            "ready_count": int(row["ready_count"] or 0),
            "error_count": int(row["error_count"] or 0),
            "pending_count": int(row["pending_count"] or 0),
            "skipped_count": int(row["skipped_count"] or 0),
            "not_covered_count": int(row["not_covered_count"] or 0),
            "escalated_count": int(row["escalated_count"] or 0),
            "avg_quality": round(float(row["avg_quality"]), 3) if row["avg_quality"] is not None else None,
            "low_quality_count": int(row["low_quality_count"] or 0),
            "high_quality_count": int(row["high_quality_count"] or 0),
            "total_chunks": int(row["total_chunks"] or 0),
            "by_kind": by_kind,
            "by_ext": by_ext,
            "by_status": by_status,
            "ingest_complete": int(row["pending_count"] or 0) == 0
            and int(row["ingestible_count"] or 0)
            == int(row["ready_count"] or 0) + int(row["error_count"] or 0) + int(row["skipped_count"] or 0),
        }

    def replace_chunks(
        self,
        file_id: int,
        case_id: int,
        rel_path: str,
        chunks: Iterable[dict[str, Any]],
        embeddings: Iterable[tuple[list[float], str]] | None = None,
    ) -> int:
        # Contentless FTS5 rows can only be removed by re-supplying the indexed values.
        old_rows = self.conn.execute(
            """
            SELECT c.id, c.text, c.bates, f.rel_path
            FROM chunks c JOIN files f ON f.id = c.file_id
            WHERE c.file_id=?
            """,
            (file_id,),
        ).fetchall()
        for r in old_rows:
            self.conn.execute(
                "INSERT INTO chunks_fts(chunks_fts, rowid, text, bates, rel_path) VALUES('delete',?,?,?,?)",
                (r["id"], r["text"], r["bates"] or "", r["rel_path"]),
            )
        self.conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM media_segments WHERE file_id=?", (file_id,))

        emb_list = list(embeddings) if embeddings is not None else None
        count = 0
        seg_index = 0
        for i, ch in enumerate(chunks):
            cur = self.conn.execute(
                """
                INSERT INTO chunks(file_id, case_id, chunk_index, text, page_start, page_end,
                                   heading, bates, start_sec, end_sec)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    file_id,
                    case_id,
                    i,
                    ch["text"],
                    ch.get("page_start"),
                    ch.get("page_end"),
                    ch.get("heading"),
                    ch.get("bates"),
                    ch.get("start_sec"),
                    ch.get("end_sec"),
                ),
            )
            for seg in ch.get("segments") or []:
                self.conn.execute(
                    """
                    INSERT INTO media_segments(file_id, case_id, seg_index, kind,
                                               start_sec, end_sec, text, speaker)
                    VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        file_id,
                        case_id,
                        seg_index,
                        seg.get("kind") or "speech",
                        float(seg["start"]),
                        float(seg["end"]),
                        seg["text"],
                        seg.get("speaker"),
                    ),
                )
                seg_index += 1
            chunk_id = cur.lastrowid
            self.conn.execute(
                "INSERT INTO chunks_fts(rowid, text, bates, rel_path) VALUES(?,?,?,?)",
                (chunk_id, ch["text"], ch.get("bates") or "", rel_path),
            )
            if emb_list is not None and i < len(emb_list):
                vec, model = emb_list[i]
                blob = json.dumps(vec).encode("utf-8")
                self.conn.execute(
                    "INSERT INTO embeddings(chunk_id, model, dim, vector) VALUES(?,?,?,?)",
                    (chunk_id, model, len(vec), blob),
                )
            count += 1
        self.conn.commit()
        return count

    def fts_search(self, case_id: int, query: str, limit: int = 20) -> list[dict[str, Any]]:
        q = sanitize_fts5_query(query)
        if not q:
            return []
        try:
            rows = self.conn.execute(
                """
                SELECT c.id AS chunk_id, c.text, c.bates, c.page_start, c.page_end, c.heading,
                       c.start_sec, c.end_sec,
                       f.rel_path, f.name, f.bates AS file_bates, f.id AS file_id,
                       bm25(chunks_fts) AS score
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                JOIN files f ON f.id = c.file_id
                WHERE c.case_id = ?
                  AND chunks_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (case_id, q, limit),
            ).fetchall()
        except sqlite3.OperationalError as e:
            # Never fail ask/search on leftover FTS syntax; caller may still use semantic.
            logger.warning("fts5 MATCH failed for %r (sanitized %r): %s", query, q, e)
            return []
        return [dict(r) for r in rows]

    def all_embeddings(self, case_id: int) -> list[tuple[int, list[float], dict[str, Any]]]:
        rows = self.conn.execute(
            """
            SELECT e.chunk_id, e.vector, c.text, c.bates, c.page_start, c.page_end, c.heading,
                   c.start_sec, c.end_sec,
                   f.rel_path, f.name, f.bates AS file_bates, f.id AS file_id
            FROM embeddings e
            JOIN chunks c ON c.id = e.chunk_id
            JOIN files f ON f.id = c.file_id
            WHERE c.case_id = ?
            """,
            (case_id,),
        ).fetchall()
        out = []
        for r in rows:
            vec = json.loads(r["vector"].decode("utf-8"))
            meta = {
                "chunk_id": r["chunk_id"],
                "text": r["text"],
                "bates": r["bates"] or r["file_bates"],
                "page_start": r["page_start"],
                "page_end": r["page_end"],
                "heading": r["heading"],
                "start_sec": r["start_sec"],
                "end_sec": r["end_sec"],
                "rel_path": r["rel_path"],
                "name": r["name"],
                "file_id": r["file_id"],
            }
            out.append((r["chunk_id"], vec, meta))
        return out

    def list_media_segments(self, file_id: int, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT kind, start_sec, end_sec, text, speaker FROM media_segments WHERE file_id=?"
        args: list[Any] = [file_id]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        sql += " ORDER BY start_sec, seg_index"
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # --- notebook (case wiki) ---
    @staticmethod
    def normalize_notebook_path(path: str) -> str:
        parts = [p.strip() for p in (path or "").replace("\\", "/").split("/") if p.strip()]
        return "/".join(parts)

    def list_notebook_pages(self, case_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, case_id, path, title, length(body) AS body_len, created_at, updated_at
            FROM notebook_pages
            WHERE case_id=?
            ORDER BY path COLLATE NOCASE
            """,
            (case_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_notebook_page(self, case_id: int, path: str) -> dict[str, Any] | None:
        norm = self.normalize_notebook_path(path)
        if not norm:
            return None
        row = self.conn.execute(
            """
            SELECT id, case_id, path, title, body, created_at, updated_at
            FROM notebook_pages WHERE case_id=? AND path=?
            """,
            (case_id, norm),
        ).fetchone()
        return dict(row) if row else None

    def get_notebook_page_by_id(self, case_id: int, page_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT id, case_id, path, title, body, created_at, updated_at
            FROM notebook_pages WHERE case_id=? AND id=?
            """,
            (case_id, page_id),
        ).fetchone()
        return dict(row) if row else None

    def upsert_notebook_page(
        self,
        case_id: int,
        path: str,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        norm = self.normalize_notebook_path(path)
        if not norm:
            raise ValueError("notebook path required")
        title = (title or norm.split("/")[-1]).strip() or norm.split("/")[-1]
        body = body if body is not None else ""
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO notebook_pages(case_id, path, title, body, created_at, updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(case_id, path) DO UPDATE SET
              title=excluded.title,
              body=excluded.body,
              updated_at=excluded.updated_at
            """,
            (case_id, norm, title, body, now, now),
        )
        self.conn.commit()
        page = self.get_notebook_page(case_id, norm)
        assert page is not None
        return page

    def delete_notebook_page(self, case_id: int, path: str) -> bool:
        norm = self.normalize_notebook_path(path)
        if not norm:
            return False
        cur = self.conn.execute(
            "DELETE FROM notebook_pages WHERE case_id=? AND path=?",
            (case_id, norm),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def ensure_notebook_home(self, case_id: int, case_name: str) -> dict[str, Any]:
        """Ensure Home + git-mirrored Meta/Skills pages. Home refreshes when stock copy is stale."""
        from .notebook import home_notebook_body, sync_skill_wiki_pages

        sync_skill_wiki_pages(self, case_id)
        # Drop obsolete single-skill page from earlier notebook chrome
        if self.get_notebook_page(case_id, "Meta/Notebook skill"):
            self.delete_notebook_page(case_id, "Meta/Notebook skill")
        body = home_notebook_body(case_name)
        existing = self.get_notebook_page(case_id, "Home")
        if existing:
            old = existing.get("body") or ""
            # Refresh stock Home when it still points at the old Meta skill / witness chrome
            if "skills/" not in old:
                return self.upsert_notebook_page(case_id, "Home", "Home", body)
            return existing
        return self.upsert_notebook_page(case_id, "Home", "Home", body)
