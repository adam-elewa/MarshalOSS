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
            """
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
    ) -> int:
        now = utc_now() if status == "ready" else None
        self.conn.execute(
            """
            INSERT INTO files(case_id, rel_path, name, bates, mime, size, mtime, sha256, status, error, ingested_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(case_id, rel_path) DO UPDATE SET
              name=excluded.name,
              bates=excluded.bates,
              mime=excluded.mime,
              size=excluded.size,
              mtime=excluded.mtime,
              sha256=excluded.sha256,
              status=excluded.status,
              error=excluded.error,
              ingested_at=COALESCE(excluded.ingested_at, files.ingested_at)
            """,
            (case_id, rel_path, name, bates, mime, size, mtime, sha256, status, error, now),
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
