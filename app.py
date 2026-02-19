"""
Whisper Studio — Flask backend
Transcription via whisper.cpp (submodule at vendor/whisper.cpp),
built with CoreML + Metal for macOS acceleration.
"""

import os
import uuid
import subprocess
import threading
import time
import platform
import json
from pathlib import Path

from flask import Flask, request, jsonify, render_template

# ── Load .env (no extra deps needed) ─────────────────────────────────────────
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
VENDOR_DIR = BASE_DIR / "vendor" / "whisper.cpp"
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
STORE_DIR = BASE_DIR / "store"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
STORE_DIR.mkdir(exist_ok=True)

JOBS_FILE = STORE_DIR / "jobs.json"

# How long to keep completed jobs (days). Set to 0 to keep forever.
JOB_TTL_DAYS = int(os.environ.get("JOB_TTL_DAYS", "30"))

IS_MACOS = platform.system() == "Darwin"


# ── Auto-discover binary ───────────────────────────────────────────────────────
def _find_whisper_bin() -> str:
    candidates = [
        os.environ.get("WHISPER_BIN", ""),
        str(VENDOR_DIR / "build" / "bin" / "whisper-cli"),
        str(VENDOR_DIR / "build" / "bin" / "main"),
        str(VENDOR_DIR / "main"),
    ]
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return c
    return ""


def _find_model() -> str:
    if os.environ.get("WHISPER_MODEL"):
        return os.environ["WHISPER_MODEL"]
    model_dir = VENDOR_DIR / "models"
    for name in [
        "ggml-base.en.bin",
        "ggml-base.bin",
        "ggml-small.en.bin",
        "ggml-small.bin",
        "ggml-medium.en.bin",
        "ggml-large-v3.bin",
    ]:
        p = model_dir / name
        if p.exists():
            return str(p)
    return ""


WHISPER_BIN = _find_whisper_bin()
WHISPER_MODEL = _find_model()

# Optional OpenAI-compatible LLM (Ollama, LM Studio, etc.)
AI_BASE_URL = os.environ.get("AI_BASE_URL", "")
AI_API_KEY = os.environ.get("AI_API_KEY", "ollama")
AI_MODEL = os.environ.get("AI_MODEL", "mistral")

# ── Helpers ───────────────────────────────────────────────────────────────────
ALLOWED = {
    "mp3",
    "wav",
    "ogg",
    "flac",
    "m4a",
    "aac",
    "opus",
    "webm",
    "caf",
    "mp4",
    "mkv",
    "mov",
    "avi",
    "ts",
    "wmv",
    "m4v",
}


def _ext(fn):
    return fn.rsplit(".", 1)[-1].lower() if "." in fn else ""


def allowed(fn):
    return _ext(fn) in ALLOWED


# ── Job store (in-memory + persisted to store/jobs.json) ──────────────────────
jobs: dict = {}
_lock = threading.Lock()


def _persist():
    """Write only completed/errored jobs to disk. Called after every state change."""
    with _lock:
        saveable = {
            jid: {k: v for k, v in j.items() if k != "debug_log"}
            for jid, j in jobs.items()
            if j["status"] in ("done", "error")
        }
    try:
        tmp = JOBS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(saveable, indent=2))
        tmp.replace(JOBS_FILE)
    except Exception as e:
        print(f"  Warning: could not persist jobs: {e}")


def _load_jobs():
    """Load persisted jobs from disk on startup, pruning old ones."""
    if not JOBS_FILE.exists():
        return
    try:
        data = json.loads(JOBS_FILE.read_text())
        cutoff = time.time() - JOB_TTL_DAYS * 86400 if JOB_TTL_DAYS > 0 else 0
        kept = 0
        for jid, j in data.items():
            if cutoff and j.get("created_at", 0) < cutoff:
                continue
            jobs[jid] = j
            kept += 1
        print(f"  Loaded {kept} jobs from store (TTL={JOB_TTL_DAYS}d)")
    except Exception as e:
        print(f"  Warning: could not load jobs store: {e}")


def new_job(filename: str) -> dict:
    j = dict(
        id=str(uuid.uuid4()),
        filename=filename,
        status="queued",
        progress=0,
        transcript=None,
        error=None,
        created_at=time.time(),
    )
    with _lock:
        jobs[j["id"]] = j
    return j


def _upd(job_id, **kw):
    should_persist = False
    with _lock:
        j = jobs.get(job_id)
        if j:
            j.update(kw)
            if kw.get("status") in ("done", "error"):
                should_persist = True
    # Persist outside the lock to avoid deadlock
    if should_persist:
        _persist()


# ── Conversion ────────────────────────────────────────────────────────────────
def to_wav(src: Path, dst: Path):
    r = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-sample_fmt",
            "s16",
            "-f",
            "wav",
            str(dst),
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{r.stderr[-800:]}")


# ── Transcription ─────────────────────────────────────────────────────────────
def transcribe(wav: Path, job_id: str) -> str:
    if not WHISPER_BIN:
        raise RuntimeError("whisper-cli binary not found — run ./setup.sh")
    if not WHISPER_MODEL:
        raise RuntimeError("No Whisper model found — run ./setup.sh")

    out_stem = str(OUTPUT_DIR / job_id)
    cmd = [
        WHISPER_BIN,
        "--model",
        WHISPER_MODEL,
        "--file",
        str(wav),
        "--output-txt",
        "--output-file",
        out_stem,
        "--print-progress",
        "--threads",
        str(max(4, os.cpu_count() or 4)),
    ]
    stderr_lines = []
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    for line in proc.stderr:
        stderr_lines.append(line)
        print(f"[whisper] {line}", end="", flush=True)
        if "progress" in line.lower() and "%" in line:
            try:
                pct = int(line.split("%")[0].split("=")[-1].strip())
                _upd(job_id, progress=30 + int(pct * 0.65))
            except ValueError:
                pass
    proc.wait()

    full_stderr = "".join(stderr_lines)
    _upd(job_id, debug_log=full_stderr)

    if proc.returncode != 0:
        raise RuntimeError(f"whisper-cli exited {proc.returncode}:\n{full_stderr}")

    txt = Path(out_stem + ".txt")
    if txt.exists():
        text = txt.read_text().strip()
        txt.unlink(missing_ok=True)
        return text
    raise RuntimeError("whisper-cli produced no output file")


# ── Worker ────────────────────────────────────────────────────────────────────
def process_job(job_id: str, src: Path):
    wav = UPLOAD_DIR / f"{job_id}.wav"
    try:
        _upd(job_id, status="converting", progress=5)
        to_wav(src, wav)
        _upd(job_id, status="transcribing", progress=30)
        text = transcribe(wav, job_id)
        _upd(job_id, status="done", progress=100, transcript=text)
    except Exception as e:
        _upd(job_id, status="error", error=str(e))
    finally:
        src.unlink(missing_ok=True)
        wav.unlink(missing_ok=True)


def start_job(job, src):
    threading.Thread(target=process_job, args=(job["id"], src), daemon=True).start()


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file"}), 400
    if not allowed(f.filename):
        return jsonify({"error": f"Unsupported type: .{_ext(f.filename)}"}), 400
    job = new_job(f.filename)
    dest = UPLOAD_DIR / f"{job['id']}_{f.filename}"
    f.save(dest)
    start_job(job, dest)
    return jsonify({"job_id": job["id"]})


@app.route("/api/record", methods=["POST"])
def record():
    blob = request.files.get("audio")
    if not blob:
        return jsonify({"error": "No audio"}), 400
    job = new_job("recording.webm")
    dest = UPLOAD_DIR / f"{job['id']}.webm"
    blob.save(dest)
    start_job(job, dest)
    return jsonify({"job_id": job["id"]})


@app.route("/api/status/<job_id>")
def status(job_id):
    j = jobs.get(job_id)
    return jsonify(j) if j else (jsonify({"error": "Not found"}), 404)


@app.route("/api/jobs")
def list_jobs():
    with _lock:
        jlist = sorted(jobs.values(), key=lambda j: j["created_at"], reverse=True)
    return jsonify(jlist[:50])


@app.route("/api/ai", methods=["POST"])
def ai_action():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    mode = data.get("mode", "summarize")  # 'summarize' | 'grammar'
    job_id = data.get("job_id")  # optional — if set, result is saved to job

    if not text:
        return jsonify({"error": "No text"}), 400
    if not AI_BASE_URL:
        return (
            jsonify(
                {
                    "error": "AI endpoint not configured",
                    "hint": (
                        "Add to your .env file:\n"
                        "  AI_BASE_URL=http://localhost:11434/v1   # Ollama\n"
                        "  AI_BASE_URL=http://localhost:1234/v1    # LM Studio\n"
                        "Then restart the server."
                    ),
                }
            ),
            503,
        )

    prompts = {
        "summarize": (
            "Provide a concise, well-structured summary of this transcript. "
            "Use bullet points for key points.\n\n" + text
        ),
        "grammar": (
            "Fix all grammar, spelling, punctuation and sentence structure issues. "
            "Preserve the original meaning and tone. Return only the corrected text.\n\n"
            + text
        ),
    }

    import urllib.request as urlreq
    import urllib.error as urlerr

    payload = json.dumps(
        {
            "model": AI_MODEL,
            "messages": [
                {"role": "user", "content": prompts.get(mode, prompts["summarize"])}
            ],
            "stream": False,
        }
    ).encode()

    url = f"{AI_BASE_URL.rstrip('/')}/chat/completions"
    req = urlreq.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AI_API_KEY}",
        },
        method="POST",
    )

    try:
        with urlreq.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
        reply = result["choices"][0]["message"]["content"]

        # Save to job if job_id provided
        if job_id:
            with _lock:
                j = jobs.get(job_id)
                if j:
                    if "ai_results" not in j:
                        j["ai_results"] = []
                    j["ai_results"].append(
                        {
                            "mode": mode,
                            "text": reply,
                            "created_at": time.time(),
                        }
                    )
            _persist()

        return jsonify({"result": reply})

    except urlerr.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"[ai] HTTP {e.code} from {url}: {body}")
        return jsonify({"error": f"Ollama returned HTTP {e.code}", "detail": body}), 503

    except urlerr.URLError as e:
        print(f"[ai] Cannot reach Ollama at {url}: {e.reason}")
        return (
            jsonify(
                {
                    "error": f"Cannot reach Ollama — is it running?",
                    "detail": str(e.reason),
                    "hint": "Run: ollama serve",
                }
            ),
            503,
        )

    except Exception as e:
        import traceback

        print(f"[ai] Unexpected error: {traceback.format_exc()}")
        return jsonify({"error": f"AI request failed: {e}"}), 503


@app.route("/api/info")
def info():
    return jsonify(
        {
            "whisper_bin": WHISPER_BIN,
            "whisper_model": WHISPER_MODEL,
            "is_macos": IS_MACOS,
            "ai_enabled": bool(AI_BASE_URL),
            "ai_model": AI_MODEL if AI_BASE_URL else None,
        }
    )


@app.route("/api/debug/<job_id>")
def debug(job_id):
    j = jobs.get(job_id)
    if not j:
        return jsonify({"error": "Not found"}), 404
    return jsonify(
        {
            "id": j["id"],
            "status": j["status"],
            "error": j.get("error"),
            "debug_log": j.get("debug_log", "(no log yet)"),
            "cmd": f"{WHISPER_BIN} --model {WHISPER_MODEL} --file <wav> --output-txt --threads {max(4, __import__('os').cpu_count() or 4)}",
        }
    )


@app.route("/api/jobs/<job_id>/ai/<int:idx>", methods=["DELETE"])
def delete_ai_result(job_id, idx):
    with _lock:
        j = jobs.get(job_id)
        if not j:
            return jsonify({"error": "Not found"}), 404
        ai_results = j.get("ai_results", [])
        if idx < 0 or idx >= len(ai_results):
            return jsonify({"error": "Index out of range"}), 400
        ai_results.pop(idx)
        j["ai_results"] = ai_results
    _persist()
    return jsonify({"ai_results": ai_results})


@app.route("/api/jobs/<job_id>", methods=["DELETE"])
def delete_job(job_id):
    with _lock:
        if job_id not in jobs:
            return jsonify({"error": "Not found"}), 404
        del jobs[job_id]
        _persist()
    return jsonify({"deleted": job_id})


@app.route("/api/jobs/clear", methods=["POST"])
def clear_jobs():
    """Delete all completed/errored jobs."""
    with _lock:
        to_remove = [jid for jid, j in jobs.items() if j["status"] in ("done", "error")]
        for jid in to_remove:
            del jobs[jid]
        _persist()
    return jsonify({"cleared": len(to_remove)})


if __name__ == "__main__":
    _load_jobs()
    print(f"  Binary : {WHISPER_BIN  or '⚠ not found — run ./setup.sh'}")
    print(f"  Model  : {WHISPER_MODEL or '⚠ not found — run ./setup.sh'}")
    print(f"  AI     : {AI_BASE_URL  or 'disabled (set AI_BASE_URL in .env)'}")
    print()
    app.run(debug=True, host="0.0.0.0", port=5343, threaded=True)
