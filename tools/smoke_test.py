#!/usr/bin/env python3
"""End-to-end smoke test: auto-ingest a mixed test case and verify the viewer API.

    .venv/bin/python tools/smoke_test.py                       # ingest + verify
    .venv/bin/python tools/smoke_test.py --skip-ingest         # verify only
    .venv/bin/python tools/smoke_test.py --root /path --slug x # another folder
    .venv/bin/python tools/smoke_test.py --heal                # re-run HQ ASR on fallbacks

What it does
  1. Starts a private `marshal serve` on 127.0.0.1:<port> (same DB as the
     main viewer, so the case shows up there too).
  2. Runs `marshal ingest <root>` exactly as a user would: zip expansion,
     manifest, Docling, OCR escalation, ASR, visual frames, entities, embeddings.
     This spends real API money (~$0.35–0.60 for a small mixed test folder);
     run `marshal estimate <root>` first if the folder changed.
  3. Checks every auto-ingest path through the HTTP API the viewer uses:
     documents, images, audio, body-cam video (BWC lanes), archives, search,
     Cellebrite UFDR chat parsing, and CSLI / tower-dump mapping.
  4. Puts the previously active case back, stops the server, prints a
     PASS/WARN/FAIL table and exits non-zero on any FAIL.

The test folder is expected to contain (any names; the checks are by type):
  PDFs + a .docx, a .zip of documents, .jpg photos, a .wav, Axon body-cam /
  fleet .mp4s, Axon background files (.kml/.xml/.st3), one or more .ufdr,
  and CSLI returns (AT&T ReportAU/ReportCT .txt, tower-dump .xlsx).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MARSHAL = REPO / ".venv" / "bin" / "marshal"

# ----------------------------------------------------------------------------- results

RESULTS: list[tuple[str, str, str, str]] = []  # (area, status, check, detail)


def record(area: str, ok: bool, check: str, detail: str = "", *, warn_only: bool = False) -> bool:
    status = "PASS" if ok else ("WARN" if warn_only else "FAIL")
    RESULTS.append((area, status, check, detail))
    mark = {"PASS": "\033[32mPASS\033[0m", "WARN": "\033[33mWARN\033[0m", "FAIL": "\033[31mFAIL\033[0m"}[status]
    print(f"  {mark}  [{area}] {check}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


# ----------------------------------------------------------------------------- http

class Api:
    def __init__(self, base: str):
        self.base = base

    def get(self, endpoint: str, /, timeout: float = 900, **params):
        # Positional-only endpoint: several APIs take a `path=` query param.
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base}{endpoint}" + (f"?{q}" if q else "")
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read()
            ctype = r.headers.get("content-type", "")
        return json.loads(body) if "json" in ctype else body.decode("utf-8", "replace")

    def post(self, path: str):
        req = urllib.request.Request(f"{self.base}{path}", method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())


def start_server(port: int, log_path: Path) -> subprocess.Popen:
    log = open(log_path, "ab")
    proc = subprocess.Popen(
        [str(MARSHAL), "serve", "--host", "127.0.0.1", "--port", str(port)],
        cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
    )
    api = Api(f"http://127.0.0.1:{port}")
    for _ in range(120):
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early (code {proc.returncode}); see {log_path}")
        try:
            if api.get("/health", timeout=2).get("ok"):
                return proc
        except Exception:
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError(f"server did not become healthy on :{port}; see {log_path}")


# ----------------------------------------------------------------------------- checks

def check_case(api: Api, slug: str) -> dict | None:
    cases = api.get("/api/cases")["cases"]
    case = next((c for c in cases if c["slug"] == slug), None)
    record("case", case is not None, f"case '{slug}' registered", case and f"id {case['id']} → {case['files_root']}" or "")
    return case


def check_manifest(api: Api, cid: int) -> list[dict]:
    m = api.get("/api/manifest", case_id=cid, limit=10000)
    recs, s = m["records"], m["summary"]
    not_covered = int(s.get("not_covered_count") or 0)
    ufdrs = [r for r in recs if str(r.get("ext") or "").lower() == ".ufdr"]
    record("manifest", s["file_count"] > 0, "production manifest built",
           f"{s['file_count']} files, {s['ingestible_count']} ingestible, {s['ready_count']} ready, "
           f"{s['error_count']} errors, {s['pending_count']} pending"
           + (f", {not_covered} unsupported types" if not_covered else "")
           + (f", {len(ufdrs)} UFDR chats-only" if ufdrs else ""))
    ing = [r for r in recs if r.get("ingestible")]
    # UFDRs are not document-ingestible; chats are handled under Forensics.
    bad = [r for r in ing if r.get("ingest_status") not in ("ready", "partial_ufdr")]
    record("manifest", not bad, "every ingestible file reached 'ready'",
           "; ".join(f"{r['rel_path']}: {r.get('ingest_status')} {r.get('error') or ''}".strip() for r in bad[:6]))
    if ufdrs:
        record("manifest", True, "UFDRs treated as chats-only (not document errors)",
               ", ".join(r.get("name") or r.get("rel_path") or "?" for r in ufdrs[:6]))
    empty = [r for r in ing if r.get("ingest_status") == "ready" and not r.get("chunk_count")
             and r.get("size", 0) > 20_000]
    record("manifest", not empty, "ready files produced text/chunks",
           ", ".join(r["rel_path"] for r in empty[:6]), warn_only=True)
    low = [r for r in ing if (r.get("quality_score") or 1) < 0.5]
    record("manifest", not low, "no very-low quality scores (<0.5)",
           ", ".join(f"{r['rel_path']}={r['quality_score']:.2f}" for r in low[:6]), warn_only=True)
    by_ext = Counter(r["ext"] for r in ing)
    record("manifest", True, "ingestible types", ", ".join(f"{k}×{v}" for k, v in sorted(by_ext.items())))

    zipped = [r for r in recs if r.get("parent_zip")]
    if any(r["ext"] == ".zip" for r in recs):
        record("archive", bool(zipped), ".zip expanded into members",
               f"{len(zipped)} members: " + ", ".join(r["name"] for r in zipped[:4]))
        zbad = [r for r in zipped if r.get("ingestible") and r.get("ingest_status") != "ready"]
        record("archive", bool(zipped) and not zbad, "zip members ingested",
               ", ".join(r["name"] for r in zbad))
    return recs


def _detail(api: Api, cid: int, rel: str) -> dict:
    return api.get("/api/files/detail", case_id=cid, path=rel)["file"]


def check_kinds(api: Api, cid: int, recs: list[dict]) -> None:
    ready = [r for r in recs if r.get("ingestible") and r.get("ingest_status") == "ready"]

    def pick(exts: set[str], min_size: int = 0):
        c = [r for r in ready if r["ext"] in exts and r.get("size", 0) >= min_size]
        return max(c, key=lambda r: r.get("chunk_count") or 0) if c else None

    plans = [
        ("pdf", {".pdf"}, 0), ("docx", {".docx", ".doc"}, 0), ("image", {".jpg", ".jpeg", ".png", ".tif", ".tiff"}, 50_000),
        ("axon-bg", {".kml", ".xml", ".st3"}, 0),
    ]
    for area, exts, min_size in plans:
        r = pick(exts, min_size)
        if not r:
            record(area, False, f"a {area} file is in the test set", "none found / none ready", warn_only=True)
            continue
        f = _detail(api, cid, r["rel_path"])
        meta = f.get("meta") if isinstance(f.get("meta"), dict) else {}
        text = " ".join((c.get("text") or "") for c in f.get("chunks") or [])
        record(area, len(text.strip()) > 20, f"text extracted · {r['name']}",
               f"{f.get('chunk_count')} chunks, {len(text):,} chars, quality {r.get('quality_score')}")
        if area == "image":
            frames = meta.get("visual_frames")
            nframes = len(frames) if isinstance(frames, list) else frames
            record(area, bool(nframes) or bool(meta.get("visual")), "visual analysis ran",
                   f"visual_frames={nframes}", warn_only=True)

    with_ents = [r for r in ready if r.get("entities_json") not in (None, "", "[]")]
    record("entities", bool(with_ents), "entity extraction populated", f"{len(with_ents)}/{len(ready)} files")


MEDIA_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}
DIARIZE_CHUNK_SEC = 1200  # hq_transcribe.DIARIZE_MAX_SEC — longer files take the chunked path


def media_report(api: Api, cid: int, recs: list[dict]) -> list[dict]:
    """Per audio/video file: status, transcript source, speech vs frame-caption volume."""
    out = []
    for r in recs:
        if r["ext"] not in MEDIA_EXTS or not r.get("ingestible"):
            continue
        row = {"rel": r["rel_path"], "name": r["name"], "status": r.get("ingest_status"),
               "error": r.get("error"), "speech_chunks": 0, "speech_chars": 0, "frames": 0,
               "timed": 0, "source": None, "asr_error": None, "duration": None, "is_video": r["ext"] in
               {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}}
        try:
            f = _detail(api, cid, r["rel_path"])
        except urllib.error.HTTPError:
            out.append(row)
            continue
        meta = f.get("meta") if isinstance(f.get("meta"), dict) else {}
        media = meta.get("media") or {}
        row.update(source=meta.get("source"), asr_error=meta.get("asr_error") or meta.get("escalate_error"),
                   duration=media.get("duration_sec") or media.get("duration"), error=f.get("error") or row["error"])
        for c in f.get("chunks") or []:
            head = (c.get("heading") or "").lower()
            if head.startswith("visual"):
                row["frames"] += 1
            else:
                row["speech_chunks"] += 1
                row["speech_chars"] += len(c.get("text") or "")
                row["timed"] += c.get("start_sec") is not None
        out.append(row)
    return out


def check_media(api: Api, cid: int, recs: list[dict], hq_expected: bool) -> list[dict]:
    rows = media_report(api, cid, recs)
    if not rows:
        record("media", False, "audio/video files in the test set", "none", warn_only=True)
        return rows
    for m in rows:
        area = "video" if m["is_video"] else "audio"
        mins = (m["duration"] or 0) / 60
        rate = f"{m['speech_chars'] / mins:.0f} chars/min" if mins else "?"
        if not record(area, m["status"] == "ready", f"{m['name']} ingested",
                      f"{m['status']}: {m['error'] or ''}".strip(": ")):
            continue
        record(area, m["speech_chunks"] > 0 and m["timed"] == m["speech_chunks"],
               f"{m['name']} has a timed transcript",
               f"{m['speech_chunks']} chunks, {m['speech_chars']:,} chars over {mins:.1f} min ({rate})")
        is_hq = str(m["source"] or "").startswith("hq:")
        if hq_expected:
            record(area, is_hq and not m["asr_error"], f"{m['name']} transcribed by HQ stack",
                   f"source={m['source']}" + (f" · fallback reason: {m['asr_error'][:140]}" if m["asr_error"] else ""),
                   warn_only=m["speech_chunks"] > 0)  # fallback text exists → WARN; nothing at all → FAIL
        if hq_expected and is_hq and (m["duration"] or 0) > DIARIZE_CHUNK_SEC:
            record(area, True, f"{m['name']} long clip ({mins:.0f} min) went through chunked diarize")
        if m["is_video"]:
            record(area, m["frames"] > 0, f"{m['name']} frame captions", f"{m['frames']} frames", warn_only=True)
    talky = [m for m in rows if m["duration"] and m["speech_chars"] / (m["duration"] / 60) >= 200]
    record("media", bool(talky), "at least one dialogue-heavy clip (≥200 chars/min) transcribed",
           ", ".join(f"{m['name']}" for m in talky[:4]) or "all clips sparse — swap in clips with dialogue",
           warn_only=True)
    return rows


def heal_fallbacks(root_slug: str, rows: list[dict], log_path: Path) -> list[str]:
    """Re-run HQ ASR (marshal retranscribe) on files that fell back or failed."""
    redo = [m for m in rows if m["status"] == "ready" and not str(m["source"] or "").startswith("hq:")]
    names = []
    with open(log_path, "a") as lf:
        for m in redo:
            print(f"    retranscribe {m['name']}", flush=True)
            rc = subprocess.call([str(MARSHAL), "retranscribe", "--slug", root_slug, "--path-contains", m["name"]],
                                 cwd=REPO, stdout=lf, stderr=subprocess.STDOUT,
                                 env={**os.environ, "NO_COLOR": "1", "COLUMNS": "200"})
            record("heal", rc == 0, f"retranscribe {m['name']}", f"rc={rc}")
            names.append(m["name"])
    return names


def preflight(root: Path) -> bool:
    ok = True
    for tool in ("ffmpeg", "ffprobe"):
        ok &= record("preflight", bool(shutil.which(tool)), f"{tool} on PATH")
    sys.path.insert(0, str(REPO / "src"))
    try:
        from marshal_oss.cli import _load_api_keys  # same key source as the CLI (~/.marshal/env.sh)
        _load_api_keys()
    except Exception as e:
        record("preflight", False, "load API keys like the CLI", str(e), warn_only=True)
    for k in ("OPENAI_API_KEY", "VOYAGE_API_KEY"):
        ok &= record("preflight", bool(os.environ.get(k)), f"{k} available")
    try:
        from marshal_oss.forensics_ufdr import ufedlib_available
        record("preflight", ufedlib_available(), "ufedlib-py installed (UFDR parsing)", warn_only=True)
    except Exception as e:
        record("preflight", False, "import marshal_oss", str(e))
        ok = False
    free = shutil.disk_usage(root).free / 1e9
    record("preflight", free > 20, "free space on case drive", f"{free:,.0f} GB", warn_only=True)
    return ok


def run_ingest(args, root: Path, log_path: Path) -> int:
    """Run `marshal ingest`, mirroring milestone lines to the console as a heartbeat."""
    keep = ("ingest ", "asr-hq", "hq transcribe", "failed", "error", "forensics", "ufdr", "visual ok",
            "media ", "retranscrib", "done", "files:", "chunks")
    t0 = time.time()
    with open(log_path, "w") as lf:
        proc = subprocess.Popen(
            [str(MARSHAL), "ingest", str(root), "--slug", args.slug, "--name", args.name, "--workers", str(args.workers)],
            cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            env={**os.environ, "NO_COLOR": "1", "COLUMNS": "200"},
        )
        assert proc.stdout
        for line in proc.stdout:
            lf.write(line)
            low = line.lower()
            if any(k in low for k in keep) and "warning" not in low:
                print(f"    {time.time() - t0:6.0f}s  {line.strip()[:150]}", flush=True)
            if time.time() - t0 > args.ingest_timeout:
                proc.kill()
                print("    ingest timed out — killed", flush=True)
                return -9
        return proc.wait()



def check_search(api: Api, cid: int, recs: list[dict]) -> None:
    pdfs = [r for r in recs if r["ext"] == ".pdf" and r.get("chunk_count")]
    if pdfs:
        target = pdfs[0]
        f = _detail(api, cid, target["rel_path"])
        words = [w.strip(".,:;()") for c in f.get("chunks") or [] for w in (c.get("text") or "").split()]
        word = next((w for w in words if len(w) >= 7 and w.isalpha()), None)
        if word:
            hits = api.get("/api/search", q=word, case_id=cid, mode="keyword")["hits"]
            record("search", any(h["rel_path"] == target["rel_path"] for h in hits),
                   f"keyword '{word}' finds {target['name']}", f"{len(hits)} hits")
    for mode in ("semantic", "hybrid"):
        hits = api.get("/api/search", q="damage to the vehicle after the crash", case_id=cid, mode=mode)["hits"]
        record("search", bool(hits), f"{mode} search returns hits",
               f"{len(hits)} hits, top: {hits[0]['name'] if hits else '-'}")


def check_bwc(api: Api, cid: int, recs: list[dict]) -> None:
    n_video = sum(1 for r in recs if r["ext"] in {".mp4", ".mov", ".avi"} and r.get("ingest_status") == "ready")
    b = api.get("/api/bwc/index", case_id=cid)
    vids = b.get("videos") or []
    record("bodycam", len(vids) == n_video and n_video > 0, "every ready video is in the BWC index",
           f"{len(vids)} in index / {n_video} ready videos, {b.get('audio_count')} audio")
    noseg = [v["filename"] for v in vids if not v.get("segment_count")]
    record("bodycam", not noseg, "each clip has transcript segments", ", ".join(noseg))
    nostart = [v["filename"] for v in vids if not v.get("recorded_start")]
    record("bodycam", not nostart, "recorded start time parsed", ", ".join(nostart), warn_only=True)
    lanes = Counter((v.get("camera_kind"), v.get("device") or v.get("officer")) for v in vids)
    record("bodycam", len(lanes) >= min(2, len(vids)), "clips split into camera lanes",
           "; ".join(f"{k[0]}:{k[1]}" for k in lanes))


def check_forensics(api: Api, cid: int, root: Path) -> None:
    s = api.get("/api/forensics/summary", case_id=cid)
    secs = s.get("sections") or {}
    ufdrs = (secs.get("cellebrite") or {}).get("sources") or []
    csli = (secs.get("csli") or {}).get("sources") or []
    disk_ufdrs = [p for p in root.rglob("*.ufdr") if not any(x.startswith(".") for x in p.relative_to(root).parts)]
    record("cellebrite", len(ufdrs) == len(disk_ufdrs) and ufdrs, "UFDRs auto-detected",
           f"{len(ufdrs)} found / {len(disk_ufdrs)} on disk")
    record("cellebrite", s.get("ufedlib") is not False, "ufedlib available on server")

    for src in ufdrs:
        t0 = time.time()
        try:
            d = api.get("/api/forensics/cellebrite/chats", path=src["rel_path"], case_id=cid, timeout=3600)
        except urllib.error.HTTPError as e:
            record("cellebrite", False, f"parse {src['name']}", f"HTTP {e.code}: {e.read()[:200]!r}")
            continue
        chats = d.get("chats") or []
        record("cellebrite", d.get("chat_count", 0) > 0 and d.get("message_count", 0) > 0,
               f"parse {src['name']}",
               f"{d.get('chat_count')} chats, {d.get('message_count')} msgs, apps {', '.join(d.get('sources') or [])} "
               f"({time.time() - t0:.0f}s)")
        record("cellebrite", any(c.get("message_days") or c.get("message_epochs") for c in chats),
               "chat list carries per-message dates (date filter)")
        busy = max(chats, key=lambda c: c.get("message_count") or 0, default=None)
        if busy:
            t = api.get("/api/forensics/cellebrite/chat", path=src["rel_path"], chat_id=busy["id"], case_id=cid)
            msgs = (t.get("chat") or {}).get("messages") or []
            stamped = sum(1 for m in msgs if m.get("timestamp"))
            record("cellebrite", len(msgs) == busy.get("message_count") and stamped,
                   "thread opens with all messages", f"{len(msgs)} msgs, {stamped} timestamped")

    record("csli", bool(csli), "CSLI / tower files auto-detected",
           ", ".join(f"{x.get('name')} ({x.get('kind')})" for x in csli[:6]))
    if csli:
        m = api.get("/api/forensics/csli/map", case_id=cid, refresh="true", timeout=1800)
        record("csli", (m.get("tower_count") or 0) > 0, "towers located", f"{m.get('tower_count')} towers")
        record("csli", (m.get("event_count") or 0) > 0, "call / ping events parsed",
               f"{m.get('event_count')} events, {m.get('time_min')} → {m.get('time_max')}")
        record("csli", bool(m.get("primary_phone")), "target phone identified", str(m.get("primary_phone")))
        record("csli", (m.get("subscribers") and len(m["subscribers"]) > 0), "subscriber names from carrier returns",
               f"{len(m.get('subscribers') or [])} subscribers", warn_only=True)


def check_viewer(api: Api) -> None:
    html = api.get("/")
    record("viewer", isinstance(html, str) and "<html" in html.lower(), "web app served", f"{len(html):,} bytes")
    record("viewer", "renderForensicsDateFilter" in html, "forensics date filter present in app.html", warn_only=True)


# ----------------------------------------------------------------------------- main

def _hq_enabled() -> bool:
    try:
        from marshal_oss.config import get_settings
        s = get_settings()
        return s.asr_provider == "openai" and bool(getattr(s, "asr_hq", False))
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=os.environ.get("MARSHAL_SMOKE_ROOT") or None,
                    help="test case folder (or set MARSHAL_SMOKE_ROOT)")
    ap.add_argument("--slug", default="smoke-test")
    ap.add_argument("--name", default="Smoke Test")
    ap.add_argument("--port", default=7341, type=int)
    ap.add_argument("--workers", default=4, type=int)
    ap.add_argument("--skip-ingest", action="store_true", help="only run the checks")
    ap.add_argument("--heal", action="store_true",
                    help="after checks, re-run HQ ASR on media that fell back, then re-check media")
    ap.add_argument("--ingest-timeout", default=4 * 3600, type=int, help="seconds before ingest is killed")
    args = ap.parse_args()
    if not args.root:
        ap.error("--root is required (or set MARSHAL_SMOKE_ROOT)")
    args.root = Path(args.root)

    root = args.root.resolve()
    if not root.is_dir():
        print(f"test folder not found: {root}", file=sys.stderr)
        return 2
    logdir = Path.home() / ".marshaloss" / "smoke"
    logdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    ingest_log, server_log = logdir / f"{stamp}-ingest.log", logdir / f"{stamp}-server.log"

    print(f"Smoke test · {root} → case '{args.slug}'\n  logs: {logdir}/{stamp}-*.log")
    print("\n▶ preflight", flush=True)
    if not preflight(root):
        print("preflight failed — fix the above first", file=sys.stderr)
        return 2
    server = start_server(args.port, server_log)
    api = Api(f"http://127.0.0.1:{args.port}")
    prev_active = (api.get("/api/cases").get("active") or {}).get("id")
    started = time.time()
    try:
        if not args.skip_ingest:
            print("\n▶ ingest (this calls OpenAI/Voyage — see `marshal estimate`)", flush=True)
            t0 = time.time()
            rc = run_ingest(args, root, ingest_log)
            tail = ingest_log.read_text(errors="replace").strip().splitlines()[-3:]
            record("ingest", rc == 0, "marshal ingest exited cleanly",
                   f"rc={rc}, {time.time() - t0:.0f}s · " + " | ".join(t.strip() for t in tail)[:300])

        print("\n▶ verify via viewer API", flush=True)
        case = check_case(api, args.slug)
        if case:
            cid = case["id"]
            recs = check_manifest(api, cid)
            hq = _hq_enabled()
            print("\n▶ audio / video", flush=True)
            rows = check_media(api, cid, recs, hq)
            if args.heal and hq:
                print("\n▶ heal: re-run HQ ASR on fallbacks", flush=True)
                if heal_fallbacks(args.slug, rows, ingest_log):
                    recs = api.get("/api/manifest", case_id=cid, limit=10000)["records"]
                    print("\n▶ audio / video (after heal)", flush=True)
                    check_media(api, cid, recs, hq)
            print("\n▶ other checks", flush=True)
            for fn, a in ((check_kinds, (recs,)), (check_search, (recs,)), (check_bwc, (recs,)),
                          (check_forensics, (root,))):
                try:
                    fn(api, cid, *a)
                except Exception as e:  # one broken area shouldn't hide the rest
                    record(fn.__name__.removeprefix("check_"), False, "check raised", f"{type(e).__name__}: {e}")
            check_viewer(api)
    finally:
        if prev_active:
            try:
                api.post(f"/api/cases/{prev_active}/activate")
            except Exception:
                pass
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()

    counts = Counter(s for _, s, _, _ in RESULTS)
    print(f"\n{counts['PASS']} pass · {counts['WARN']} warn · {counts['FAIL']} fail · {time.time() - started:.0f}s")
    report = logdir / f"{stamp}-report.json"
    report.write_text(json.dumps([dict(zip(("area", "status", "check", "detail"), r)) for r in RESULTS], indent=1))
    print(f"report: {report}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
