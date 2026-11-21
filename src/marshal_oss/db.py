from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


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
              UNIQUE(file_id, chunk_index)
            );
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
        # Clear old FTS rows for this file's chunks
        old_ids = [
            r["id"]
            for r in self.conn.execute(
                "SELECT id FROM chunks WHERE file_id=?", (file_id,)
            ).fetchall()
        ]
        for cid in old_ids:
            self.conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
        self.conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))

        emb_list = list(embeddings) if embeddings is not None else None
        count = 0
        for i, ch in enumerate(chunks):
            cur = self.conn.execute(
                """
                INSERT INTO chunks(file_id, case_id, chunk_index, text, page_start, page_end, heading, bates)
                VALUES(?,?,?,?,?,?,?,?)
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
                ),
            )
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
        # Simple FTS: escape quotes
        q = query.replace('"', " ").strip()
        if not q:
            return []
        rows = self.conn.execute(
            """
            SELECT c.id AS chunk_id, c.text, c.bates, c.page_start, c.page_end, c.heading,
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
        return [dict(r) for r in rows]

    def all_embeddings(self, case_id: int) -> list[tuple[int, list[float], dict[str, Any]]]:
        rows = self.conn.execute(
            """
            SELECT e.chunk_id, e.vector, c.text, c.bates, c.page_start, c.page_end, c.heading,
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
                "rel_path": r["rel_path"],
                "name": r["name"],
                "file_id": r["file_id"],
            }
            out.append((r["chunk_id"], vec, meta))
        return out
