#!/usr/bin/env python3
"""Wayword local server. Python standard library only.

Run from anywhere:
  python3 server/server.py
Then open http://127.0.0.1:5173
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import matcher

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
PRIVATE = ROOT / "data" / "private"
DB_PATH = PRIVATE / "wayword.db"
PROMPTS_PATH = ROOT / "data" / "prompts.json"
MANIFEST_PATH = ROOT / "data" / "demo_manifest.json"
TRANSCRIBE = ROOT / "server" / "WaywordSpeech.app" / "Contents" / "MacOS" / "transcribe"
HOST = "127.0.0.1"
PORT = 5173
MAX_UPLOAD = 15_000_000
SPEECH_LOCK = threading.Lock()
SPEECH_REV = "2"

BEATS = [
    "quiz",
    "talk",
    "worcester",
    "south-church",
    "whole-foods",
    "whole-foods-replay",
    "history",
    "gloucester",
    "results",
]

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".wav": "audio/wav",
    ".json": "application/json",
    ".svg": "image/svg+xml",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def load_json(path):
    return json.loads(Path(path).read_text())


def connect():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS transcripts (
            sha256 TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS memory (
            user_id TEXT NOT NULL,
            heard TEXT NOT NULL,
            place_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, heard)
        )
        """
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(memory)")}
    if "kind" not in columns:
        db.execute("ALTER TABLE memory ADD COLUMN kind TEXT NOT NULL DEFAULT 'choice'")
    db.commit()
    return db


def clip_file(rel):
    rel = unquote(rel or "").replace("\\", "/").lstrip("/")
    parts = [part for part in rel.split("/") if part]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("bad clip path")
    path = (PRIVATE.joinpath(*parts)).resolve()
    if PRIVATE.resolve() not in path.parents:
        raise ValueError("bad clip path")
    return path


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_digest(path):
    return file_hash(path) + ":" + SPEECH_REV


def contextual_phrases(vocab):
    seen = set()
    phrases = []
    for place in vocab["places"]:
        for text in (place.get("match"), place.get("local_as")):
            phrase = (text or "").strip()
            key = phrase.lower()
            if not phrase or key in seen:
                continue
            seen.add(key)
            phrases.append(phrase)
    return phrases


def write_hints(vocab):
    path = Path(tempfile.mkstemp(suffix=".txt")[1])
    path.write_text("\n".join(contextual_phrases(vocab)) + "\n")
    return path


def cached_transcript(db, digest):
    row = db.execute("SELECT payload FROM transcripts WHERE sha256 = ?", (digest,)).fetchone()
    if not row:
        return None
    return json.loads(row["payload"])


def store_transcript(db, digest, payload):
    db.execute(
        "INSERT OR REPLACE INTO transcripts (sha256, payload, created_at) VALUES (?, ?, ?)",
        (digest, json.dumps(payload), now()),
    )
    db.commit()


def normalize_wav(src):
    dest = Path(tempfile.mkstemp(suffix=".wav")[1])
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(src), str(dest)],
        check=True,
        capture_output=True,
    )
    return dest


def run_speech(wav_path, hints_path):
    # LaunchServices has to start the app. Running the binary directly makes
    # macOS miss the Speech usage description and abort.
    if not TRANSCRIBE.exists():
        raise RuntimeError("Speech tool is not built. Run: python3 server/server.py --build-speech")
    out = Path(tempfile.mkstemp(suffix=".json")[1])
    app = TRANSCRIBE.parents[2]
    try:
        try:
            proc = subprocess.run(
                ["open", "-W", "-n", str(app), "--args", str(wav_path.resolve()), str(out), str(hints_path)],
                capture_output=True,
                timeout=70,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Apple Speech took too long. Try the recording again.") from exc
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            message = proc.stderr.decode("utf-8", "replace").strip()
            if not message:
                message = (
                    "Apple Speech was blocked. Allow Speech Recognition for WaywordSpeech "
                    "in System Settings, Privacy & Security, Speech Recognition."
                )
            raise RuntimeError(message)
        return json.loads(out.read_text())
    finally:
        out.unlink(missing_ok=True)


def transcribe_file(db, path):
    digest = cache_digest(path)
    cached = cached_transcript(db, digest)
    if cached:
        cached["cached"] = True
        return cached
    wav = normalize_wav(path)
    hints = None
    try:
        hints = write_hints(matcher.load_vocab())
        with SPEECH_LOCK:
            payload = run_speech(wav, hints)
    finally:
        wav.unlink(missing_ok=True)
        if hints is not None:
            hints.unlink(missing_ok=True)
    payload["cached"] = False
    store_transcript(db, digest, payload)
    return payload


def memory_hit(db, user_id, texts):
    if not user_id:
        return None, None
    rows = db.execute("SELECT heard, place_id, kind FROM memory WHERE user_id = ?", (user_id,)).fetchall()
    saved = {row["heard"]: (row["place_id"], row["kind"] or "choice") for row in rows}
    for text in texts:
        key = matcher.normalize_heard(text)
        if key in saved:
            return saved[key]
    return None, None


def hear(db, audio_path, user_id, vocab):
    payload = transcribe_file(db, audio_path)
    texts = [row.get("text", "") for row in payload.get("transcripts", [])]
    place_id, kind = memory_hit(db, user_id, texts)
    result = matcher.public_result(
        matcher.match(payload.get("transcripts", []), vocab, vocab["origin"], place_id, kind)
    )
    result["cached"] = bool(payload.get("cached"))
    result["confidences"] = [row.get("confidence") for row in payload.get("transcripts", [])]
    return result


def save_memory(db, user_id, heard, place_id, kind, vocab):
    key = matcher.normalize_heard(heard)
    if kind not in ("choice", "correction"):
        raise ValueError("bad memory kind")
    if not user_id or not key or not place_id:
        raise ValueError("user_id, heard, and place_id are required")
    if not any(place["id"] == place_id for place in vocab["places"]):
        raise ValueError("unknown place")
    db.execute(
        """
        INSERT INTO memory (user_id, heard, place_id, created_at, kind)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, heard) DO UPDATE SET
            place_id = excluded.place_id,
            created_at = excluded.created_at,
            kind = excluded.kind
        """,
        (user_id, key, place_id, now(), kind),
    )
    db.commit()


def history(db, user_id, vocab):
    names = {place["id"]: place["name"] for place in vocab["places"]}
    rows = db.execute(
        "SELECT heard, place_id, created_at FROM memory WHERE user_id = ? ORDER BY created_at",
        (user_id,),
    ).fetchall()
    return [
        {"heard": row["heard"], "place_id": row["place_id"], "name": names.get(row["place_id"], row["place_id"])}
        for row in rows
    ]


def reset_demo(db, user_id="demo"):
    db.execute("DELETE FROM memory WHERE user_id = ?", (user_id,))
    db.commit()


def recorded_takes():
    prompts = load_json(PROMPTS_PATH)
    found = {}
    for prompt in prompts:
        takes = []
        for speaker in ("speaker_1", "speaker_2"):
            for take in range(1, int(prompt["takes"]) + 1):
                path = PRIVATE / speaker / f"{prompt['id']}_{take}.wav"
                if path.exists():
                    takes.append({"speaker": speaker, "take": take, "clip": f"{speaker}/{prompt['id']}_{take}.wav"})
        found[prompt["id"]] = takes
    return prompts, found


def prompt_by_id(prompt_id):
    for prompt in load_json(PROMPTS_PATH):
        if prompt["id"] == prompt_id:
            return prompt
    return None


def correct(prompt, outcome, place_id, cards):
    if prompt.get("ask_chain"):
        chain_ids = {
            place["id"]
            for place in matcher.load_vocab()["places"]
            if place.get("chain") == prompt["ask_chain"]
        }
        card_ids = {card["id"] for card in cards}
        return outcome == "ask" and card_ids <= chain_ids and len(card_ids) == 2
    return outcome == "place" and place_id == prompt.get("place_id")


def evaluate(db, vocab):
    prompts, found = recorded_takes()
    by_id = {prompt["id"]: prompt for prompt in prompts}
    eval_ids = {prompt["id"] for prompt in prompts if prompt["set"] == "eval"}
    video_ids = {prompt["id"] for prompt in prompts if prompt["set"] == "video"}
    have_eval = any(item["speaker"] for prompt_id in eval_ids for item in found.get(prompt_id, []))
    use_ids = eval_ids if have_eval else video_ids
    caption = None if have_eval else "Eval recordings are not in yet. These numbers are the video set only."
    per_speaker = {"speaker_1": [], "speaker_2": []}
    heatmap = {}
    any_clip = False
    for prompt_id in use_ids:
        prompt = by_id[prompt_id]
        place = next((item for item in vocab["places"] if item["id"] == prompt.get("place_id")), None)
        intended = place["local_phones"] if place else matcher.word_phones(prompt["text"])
        for item in found.get(prompt_id, []):
            if prompt["set"] == "video" and item["take"] != 1 and not have_eval:
                continue
            if prompt["set"] == "video" and have_eval:
                continue
            path = clip_file(item["clip"])
            if not path.exists():
                continue
            any_clip = True
            try:
                payload = transcribe_file(db, path)
            except RuntimeError as exc:
                per_speaker[item["speaker"]].append({"id": prompt_id, "error": str(exc)})
                continue
            texts = [row.get("text", "") for row in payload.get("transcripts", [])]
            top = texts[0] if texts else ""
            plain = matcher.plain_lookup(top, vocab, vocab["origin"]) if top else None
            result = matcher.match(payload.get("transcripts", []), vocab, vocab["origin"], None)
            wayword_ok = correct(
                prompt,
                result["outcome"],
                result["place"]["id"] if result["place"] else None,
                result["cards"],
            )
            plain_ok = bool(plain and prompt.get("place_id") and plain["id"] == prompt["place_id"])
            heard_phones = matcher.word_phones(matcher.strip_carrier(top))
            for left, right in matcher.substitutions(intended, heard_phones, vocab["swaps"]):
                heatmap[f"{left}>{right}"] = heatmap.get(f"{left}>{right}", 0) + 1
            per_speaker[item["speaker"]].append(
                {
                    "id": prompt_id,
                    "text": prompt["text"],
                    "heard": result["heard"],
                    "plain_ok": plain_ok,
                    "wayword_ok": wayword_ok,
                    "outcome": result["outcome"],
                    "place": result["place"]["name"] if result["place"] else None,
                }
            )
    def rate(rows, key):
        graded = [row for row in rows if "error" not in row]
        if not graded:
            return None
        return round(100 * sum(1 for row in graded if row[key]) / len(graded))

    speakers = []
    for speaker, rows in per_speaker.items():
        graded = [row for row in rows if "error" not in row]
        if not graded:
            continue
        speakers.append(
            {
                "id": speaker,
                "n": len(graded),
                "plain": rate(rows, "plain_ok"),
                "wayword": rate(rows, "wayword_ok"),
                "clips": graded,
            }
        )
    heat_rows = [
        {"from": pair.split(">")[0], "to": pair.split(">")[1], "count": count}
        for pair, count in sorted(heatmap.items(), key=lambda item: item[1], reverse=True)
    ]
    return {
        "ready": any_clip,
        "caption": caption if any_clip else "No recordings yet.",
        "source": "eval" if have_eval else "video",
        "speakers": speakers,
        "heatmap": heat_rows[:12],
    }


class App(BaseHTTPRequestHandler):
    vocab = None

    def log_message(self, fmt, *args):
        print("%s %s" % (self.address_string(), fmt % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api("GET", parsed)
            return
        self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api("POST", parsed)
            return
        self.send_error(404)

    def handle_api(self, method, parsed):
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/prompts" and method == "GET":
                prompts, found = recorded_takes()
                self.send_json({"prompts": prompts, "recorded": found, "beats": BEATS})
                return
            if parsed.path == "/api/manifest" and method == "GET":
                manifest = load_json(MANIFEST_PATH)
                beats = {}
                for beat, rel in manifest["beats"].items():
                    beats[beat] = {"clip": rel, "ready": clip_file(rel).exists()}
                self.send_json({"user_id": manifest["user_id"], "beats": beats})
                return
            if parsed.path == "/api/clips" and method == "GET":
                _, found = recorded_takes()
                self.send_json({"recorded": found})
                return
            if parsed.path == "/api/audio" and method == "GET":
                path = clip_file(query.get("clip", [""])[0])
                if not path.exists():
                    self.send_error(404)
                    return
                self.send_bytes(path.read_bytes(), "audio/wav")
                return
            if parsed.path == "/api/places" and method == "GET":
                self.send_json({"places": matcher.search_places(self.vocab, query.get("q", [""])[0])})
                return
            if parsed.path == "/api/history" and method == "GET":
                user_id = query.get("user_id", ["demo"])[0]
                with connect() as db:
                    self.send_json({"items": history(db, user_id, self.vocab)})
                return
            if parsed.path == "/api/eval" and method == "GET":
                with connect() as db:
                    self.send_json(evaluate(db, self.vocab))
                return
            if parsed.path == "/api/hear" and method == "POST":
                if self.headers.get("Content-Type", "").startswith("audio/wav"):
                    user_id = query.get("user_id", ["demo"])[0]
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > MAX_UPLOAD:
                        self.send_json({"error": "empty or oversized recording"}, 400)
                        return
                    data = self.rfile.read(length)
                    fd, name = tempfile.mkstemp(suffix=".wav")
                    os.close(fd)
                    dest = Path(name)
                    try:
                        dest.write_bytes(data)
                        with connect() as db:
                            self.send_json(hear(db, dest, user_id, self.vocab))
                    finally:
                        dest.unlink(missing_ok=True)
                    return
                body = self.read_json()
                user_id = body.get("user_id") or "demo"
                rel = body.get("clip")
                if body.get("beat"):
                    rel = load_json(MANIFEST_PATH)["beats"][body["beat"]]
                path = clip_file(rel)
                if not path.exists():
                    self.send_json({"error": "Record this clip first.", "missing": rel}, 404)
                    return
                with connect() as db:
                    self.send_json(hear(db, path, user_id, self.vocab))
                return
            if parsed.path == "/api/choose" and method == "POST":
                body = self.read_json()
                with connect() as db:
                    save_memory(
                        db,
                        body.get("user_id") or "demo",
                        body.get("heard", ""),
                        body.get("place_id", ""),
                        "choice",
                        self.vocab,
                    )
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/correct" and method == "POST":
                body = self.read_json()
                with connect() as db:
                    save_memory(
                        db,
                        body.get("user_id") or "demo",
                        body.get("heard", ""),
                        body.get("place_id", ""),
                        "correction",
                        self.vocab,
                    )
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/reset-demo" and method == "POST":
                body = self.read_json() if self.headers.get("Content-Length") else {}
                with connect() as db:
                    reset_demo(db, (body or {}).get("user_id") or "demo")
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/record" and method == "POST":
                speaker = query.get("speaker", [""])[0]
                prompt_id = query.get("prompt", [""])[0]
                take = int(query.get("take", ["1"])[0])
                prompt = prompt_by_id(prompt_id)
                if speaker not in ("speaker_1", "speaker_2") or not prompt or take < 1 or take > int(prompt["takes"]):
                    self.send_json({"error": "bad recording target"}, 400)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_UPLOAD:
                    self.send_json({"error": "empty or oversized recording"}, 400)
                    return
                data = self.rfile.read(length)
                folder = PRIVATE / speaker
                folder.mkdir(parents=True, exist_ok=True)
                dest = folder / f"{prompt_id}_{take}.wav"
                dest.write_bytes(data)
                self.send_json({"ok": True, "clip": f"{speaker}/{prompt_id}_{take}.wav"})
                return
        except KeyError as exc:
            self.send_json({"error": "missing %s" % exc}, 400)
            return
        except (ValueError, RuntimeError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
            self.send_json({"error": str(exc)}, 500)
            return
        self.send_error(404)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, path):
        rel = unquote(path.split("?", 1)[0])
        if rel in ("", "/"):
            rel = "/index.html"
        parts = [part for part in rel.split("/") if part]
        if any(part == ".." for part in parts):
            self.send_error(403)
            return
        file_path = (WEB.joinpath(*parts)).resolve()
        if WEB.resolve() not in file_path.parents and file_path != WEB.resolve():
            self.send_error(403)
            return
        if not file_path.is_file():
            self.send_error(404)
            return
        self.send_bytes(file_path.read_bytes(), MIME.get(file_path.suffix, "application/octet-stream"))


def version_pair(text):
    nums = [int(part) for part in text.split(".") if part.isdigit()]
    while len(nums) < 2:
        nums.append(0)
    return tuple(nums[:2])


def swift_module_overlay():
    # Some Command Line Tools installs define SwiftBridging twice, which makes
    # swiftc fail before it can build this helper. Hide the duplicate map.
    swiftc = subprocess.run(["xcrun", "--find", "swiftc"], check=True, capture_output=True, text=True)
    include = Path(swiftc.stdout.strip()).resolve().parents[1] / "include"
    duplicate = include / "swift" / "module.modulemap"
    bridging = include / "swift" / "bridging.modulemap"
    parent = include / "module.modulemap"
    if not (duplicate.is_file() and bridging.is_file() and parent.is_file()):
        return [], []
    if "bridging.modulemap" not in parent.read_text(encoding="utf-8"):
        return [], []
    empty = Path(tempfile.mkstemp(prefix="wayword-module-", suffix=".modulemap")[1])
    overlay = Path(tempfile.mkstemp(prefix="wayword-vfs-", suffix=".json")[1])
    empty.write_text("\n")
    overlay.write_text(
        json.dumps(
            {
                "version": 0,
                "roots": [
                    {
                        "name": str(duplicate),
                        "type": "file",
                        "external-contents": str(empty),
                    }
                ],
            }
        )
    )
    return ["-vfsoverlay", str(overlay), "-Xcc", "-ivfsoverlay", "-Xcc", str(overlay)], [empty, overlay]


def speech_ready():
    if not TRANSCRIBE.is_file():
        return False
    signed = subprocess.run(["codesign", "-dv", str(TRANSCRIBE.parents[2])], capture_output=True, text=True)
    if "Info.plist=not bound" in signed.stdout + signed.stderr:
        return False
    proc = subprocess.run(["vtool", "-show-build", str(TRANSCRIBE)], capture_output=True, text=True)
    host = version_pair(platform.mac_ver()[0] or "0")
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "minos":
            return version_pair(parts[1]) <= host
    return False


def build_speech():
    binary = TRANSCRIBE
    app = binary.parents[2]
    binary.parent.mkdir(parents=True, exist_ok=True)
    source = ROOT / "server" / "transcribe.swift"
    extra, temps = swift_module_overlay()
    try:
        subprocess.run(["swiftc", "-O", *extra, "-o", str(binary), str(source)], check=True)
    finally:
        for path in temps:
            path.unlink(missing_ok=True)
    subprocess.run(["xattr", "-cr", str(app)], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    print("built", binary)


def main():
    import sys

    if "--build-speech" in sys.argv:
        build_speech()
        return
    if not speech_ready():
        build_speech()
    App.vocab = matcher.load_vocab()
    connect().close()
    server = ThreadingHTTPServer((HOST, PORT), App)
    print("Wayword http://%s:%s" % (HOST, PORT), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
