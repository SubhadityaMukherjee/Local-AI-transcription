"""
Whisper Studio — Flask routes
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib import error as urlerr

from flask import Flask, Response, jsonify, render_template, request, send_file

# ---------------------------------------------------------------------------
# Internal helpers (module-level so they're shared between route functions)
# ---------------------------------------------------------------------------

_cancel_events: dict[str, threading.Event] = {}
_transcription_progress: dict[str, dict] = {}
_progress_lock = threading.Lock()

_job_queue: list[tuple[str, Path]] = []
_queue_cv = threading.Condition()


def _set_progress(job_id: str, payload: dict) -> None:
    with _progress_lock:
        _transcription_progress[job_id] = payload


def _get_progress(job_id: str) -> dict | None:
    with _progress_lock:
        return _transcription_progress.get(job_id)


def _make_cancel_event(job_id: str) -> threading.Event:
    ev = threading.Event()
    _cancel_events[job_id] = ev
    return ev


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _ext_from_mime(mime: str) -> str:
    return "wav"


# ---------------------------------------------------------------------------
# Job runner / queue
# ---------------------------------------------------------------------------


def _run_job(job_id: str, src: Path, job_store, transcription_service) -> None:
    job_store.update(job_id, status="converting", progress=5)
    _set_progress(
        job_id,
        {
            "stage": "converting",
            "pct": 5,
            "message": "Converting audio file…",
            "start_time": time.time(),
        },
    )

    def on_progress(data: dict | int) -> None:
        if isinstance(data, dict):
            pct = data.get("pct", 0)
            stage = data.get("stage", "transcribing")
            msg = data.get("message", f"Transcribing… {pct}%")
            seg = data.get("text_segment")
        else:
            pct = data
            stage = "transcribing" if pct >= 30 else "converting"
            msg = (
                f"{'Transcribing' if stage == 'transcribing' else 'Converting'}… {pct}%"
            )
            seg = None

        job_store.update(job_id, progress=pct, status=stage)
        prev_start = (_get_progress(job_id) or {}).get("start_time", time.time())
        payload = {"stage": stage, "pct": pct, "message": msg, "start_time": prev_start}
        if seg:
            payload["text_segment"] = seg
        _set_progress(job_id, payload)

    cancel_ev = _make_cancel_event(job_id)
    transcription_service._cancel_event = cancel_ev

    try:
        vstatus, result = transcription_service.process(src, job_id, on_progress)
        if vstatus == "done":
            job_store.update(job_id, status="done", progress=100, transcript=result)
            prog = _get_progress(job_id) or {}
            prog.update({"stage": "done", "pct": 100})
            _set_progress(job_id, prog)
        elif vstatus == "cancelled":
            job_store.update(job_id, status="cancelled", error="Cancelled by user")
            prog = _get_progress(job_id) or {}
            prog.update({"stage": "cancelled", "message": "Cancelled by user"})
            _set_progress(job_id, prog)
        else:
            job_store.update(job_id, status="error", error=result)
            prog = _get_progress(job_id) or {}
            prog.update({"stage": "error", "message": f"Error: {result}"})
            _set_progress(job_id, prog)
    finally:
        _cancel_events.pop(job_id, None)
        if hasattr(transcription_service, "_cancel_event"):
            delattr(transcription_service, "_cancel_event")


def _enqueue(job_id: str, src: Path) -> None:
    with _queue_cv:
        _job_queue.append((job_id, src))
        _queue_cv.notify()


def _save_recording_gradio(
    config, src: Path, job_id: str, ext: str
) -> tuple[Path, Path]:
    """Copy a Gradio-provided audio file into recordings + uploads dirs."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    saved = config.RECORDINGS_DIR / f"{ts}_{job_id[:8]}.{ext}"
    dest = config.UPLOAD_DIR / f"{job_id}.{ext}"
    shutil.copy2(src, saved)
    shutil.copy2(src, dest)
    return saved, dest


def _start_queue_worker(job_store, transcription_service) -> None:
    def worker():
        while True:
            with _queue_cv:
                while not _job_queue:
                    _queue_cv.wait()
                job_id, src = _job_queue.pop(0)
            try:
                _run_job(job_id, src, job_store, transcription_service)
            except Exception as exc:
                print(f"[queue] error processing {job_id}: {exc}")

    threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------


def _save_recording(blob, config, job_id: str, ext: str) -> tuple[Path, Path]:
    """Save uploaded blob to recordings dir and uploads dir. Returns (saved, dest)."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    saved = config.RECORDINGS_DIR / f"{ts}_{job_id[:8]}.{ext}"
    blob.save(saved)
    dest = config.UPLOAD_DIR / f"{job_id}.{ext}"
    shutil.copy2(saved, dest)
    return saved, dest


def _concat_audio(src1: Path, src2: Path, out: Path) -> bool:
    """Concatenate two audio files via ffmpeg. Returns True on success."""
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src1),
                "-i",
                str(src2),
                "-filter_complex",
                "[0:a][1:a]concat=n=2:v=0:a=1[a]",
                "-map",
                "[a]",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and out.exists()
    except Exception as exc:
        print(f"[ffmpeg] concat error: {exc}")
        return False


# ---------------------------------------------------------------------------
# AI helpers
# ---------------------------------------------------------------------------


def _transcribe_audio_blob(blob, config, transcription_service) -> tuple[str, str]:
    """Transcribe an uploaded audio blob. Returns (status, transcript_or_error)."""
    mime = blob.mimetype or blob.content_type or ""
    suffix = f".{_ext_from_mime(mime)}"
    tmp = Path(tempfile.gettempdir()) / f"voice_edit_{uuid.uuid4()}{suffix}"
    blob.save(str(tmp))
    try:
        return transcription_service.process(
            tmp, f"voice_edit_{uuid.uuid4()}", lambda _: None
        )
    finally:
        tmp.unlink(missing_ok=True)


def _handle_ai_errors(exc: Exception) -> tuple[dict, int]:
    if isinstance(exc, urlerr.HTTPError):
        body = exc.read().decode(errors="replace")
        print(f"[ai] HTTP {exc.code}: {body}")
        return {"error": f"Ollama returned HTTP {exc.code}", "detail": body}, 503
    if isinstance(exc, urlerr.URLError):
        print(f"[ai] Cannot reach Ollama: {exc.reason}")
        return {
            "error": "Cannot reach Ollama — is it running?",
            "detail": str(exc.reason),
            "hint": "Run: ollama serve",
        }, 503
    import traceback

    print(f"[ai] Unexpected error: {traceback.format_exc()}")
    return {"error": f"AI request failed: {exc}"}, 503


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def register_routes(
    app: Flask, config, job_store, transcription_service, ai_service
) -> None:
    """Attach all routes to *app*."""

    _start_queue_worker(job_store, transcription_service)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        return render_template("index.html")

    # ------------------------------------------------------------------
    # Upload & record
    # ------------------------------------------------------------------

    @app.route("/api/upload", methods=["POST"])
    def upload():
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"error": "No file"}), 400
        if not config.allowed_file(f.filename):
            return (
                jsonify(
                    {"error": f"Unsupported type: .{config.file_extension(f.filename)}"}
                ),
                400,
            )

        job = job_store.create(f.filename)
        dest = config.UPLOAD_DIR / f"{job['id']}_{f.filename}"
        f.save(dest)
        _enqueue(job["id"], dest)
        return jsonify({"job_id": job["id"]})

    @app.route("/api/record", methods=["POST"])
    def record():
        blob = request.files.get("audio")
        if not blob:
            return jsonify({"error": "No audio"}), 400

        append_to = request.form.get("append_to")
        mime = blob.mimetype or blob.content_type or ""
        ext = _ext_from_mime(mime)

        if append_to:
            return _handle_append(blob, append_to, ext)

        ts = time.strftime("%Y%m%d_%H%M%S")
        job = job_store.create(f"recording_{ts}.{ext}")
        saved, dest = _save_recording(blob, config, job["id"], ext)

        size = saved.stat().st_size
        if size < 1000:
            saved.unlink(missing_ok=True)
            dest.unlink(missing_ok=True)
            job_store.update(
                job["id"],
                status="error",
                error=f"Recording too small ({size} bytes) — was the mic captured?",
            )
            return jsonify({"job_id": job["id"]})

        job_store.update(job["id"], recording=str(saved.relative_to(config.BASE_DIR)))
        _enqueue(job["id"], dest)
        return jsonify({"job_id": job["id"]})

    def _handle_append(blob, append_to: str, ext: str):
        existing = job_store.get(append_to)
        if not existing:
            return jsonify({"error": "Job not found for append"}), 404

        rec_rel = existing.get("recording")
        if not rec_rel:
            return (
                jsonify(
                    {
                        "error": "No recording found for append",
                        "hint": "This job was created from an uploaded file without a saved recording.",
                    }
                ),
                404,
            )

        existing_path = config.BASE_DIR / rec_rel
        if not existing_path.exists():
            return jsonify({"error": "Recording file not found on disk"}), 404

        ts = time.strftime("%Y%m%d_%H%M%S")
        new_clip = config.RECORDINGS_DIR / f"append_{ts}.{ext}"
        blob.save(new_clip)

        if new_clip.stat().st_size < 1000:
            new_clip.unlink(missing_ok=True)
            return jsonify({"error": "Appended recording too small"}), 400

        combined = config.RECORDINGS_DIR / f"combined_{ts}.{ext}"
        if not _concat_audio(existing_path, new_clip, combined):
            print("[record] ffmpeg concat failed — using new recording only")
            shutil.copy2(new_clip, combined)

        new_clip.unlink(missing_ok=True)

        job_store.update(
            append_to,
            recording=str(combined.relative_to(config.BASE_DIR)),
            transcript=None,
            status="queued",
            progress=0,
            error=None,
        )
        dest = config.UPLOAD_DIR / f"{append_to}.{ext}"
        shutil.copy2(combined, dest)
        _enqueue(append_to, dest)
        return jsonify({"job_id": append_to})

    # ------------------------------------------------------------------
    # Job status / management
    # ------------------------------------------------------------------

    @app.route("/api/status/<job_id>")
    def status(job_id):
        j = job_store.get(job_id)
        return jsonify(j) if j else (jsonify({"error": "Not found"}), 404)

    @app.route("/api/jobs")
    def list_jobs():
        return jsonify(job_store.list_all())

    @app.route("/api/jobs/<job_id>", methods=["DELETE"])
    def delete_job(job_id):
        return (
            jsonify({"deleted": job_id})
            if job_store.delete(job_id)
            else (jsonify({"error": "Not found"}), 404)
        )

    @app.route("/api/jobs/clear", methods=["POST"])
    def clear_jobs():
        return jsonify({"cleared": job_store.clear_completed()})

    @app.route("/api/jobs/<job_id>/ai/<int:idx>", methods=["DELETE"])
    def delete_ai_result(job_id, idx):
        if job_store.delete_ai_result(job_id, idx):
            return jsonify({"ai_results": job_store.get(job_id).get("ai_results", [])})
        return jsonify({"error": "Not found"}), 404

    @app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
    def cancel_job(job_id):
        ev = _cancel_events.get(job_id)
        if ev:
            ev.set()
            return jsonify({"cancelled": job_id})
        return jsonify({"error": "No active job"}), 404

    @app.route("/api/jobs/<job_id>/download")
    def download_recording(job_id):
        j = job_store.get(job_id)
        if not j or not j.get("recording"):
            return jsonify({"error": "No recording saved for this job"}), 404
        path = config.BASE_DIR / j["recording"]
        if not path.exists():
            return jsonify({"error": "Recording file not found on disk"}), 404
        return send_file(path, as_attachment=True, download_name=path.name)

    # ------------------------------------------------------------------
    # AI modes
    # ------------------------------------------------------------------

    @app.route("/api/ai/modes", methods=["GET"])
    def get_ai_modes():
        return jsonify(
            {
                "modes": ai_service.mode_info(),
                "order": ai_service.available_modes(),
            }
        )

    @app.route("/api/ai/modes", methods=["POST"])
    def create_ai_mode():
        data = request.get_json(force=True)
        mode = data.get("mode")
        prompt_conf = data.get("config")
        if not mode or not isinstance(prompt_conf, dict):
            return jsonify({"error": "mode and config required"}), 400
        try:
            ai_service.add_mode(mode, prompt_conf)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            print(f"[ai_modes] write error: {exc}")
            return jsonify({"error": "Failed to save mode"}), 500
        return jsonify({"success": True})

    # ------------------------------------------------------------------
    # AI actions
    # ------------------------------------------------------------------

    def _resolve_ai_input(req) -> tuple[str, str, str, list | None] | Response:
        """
        Parse request (multipart or JSON) and return
        (full_text, mode, job_id, personal_names) or a Flask error Response.
        """
        audio = req.files.get("audio")
        if audio:
            text = (req.form.get("text") or "").strip()
            mode = req.form.get("mode", "edit")
            job_id = req.form.get("job_id", "")
            vstatus, result = _transcribe_audio_blob(
                audio, config, transcription_service
            )
            if vstatus != "done":
                return jsonify({"error": f"Failed to transcribe audio: {result}"}), 500
            full_text = f"Original text:\n{text}\n\nVoice command:\n{result}"
            return full_text, mode, job_id, None

        data = req.get_json(force=True)
        full_text = (data.get("text") or "").strip()
        mode = data.get("mode", "summarize")
        job_id = data.get("job_id", "")
        names = data.get("personal_names")
        return full_text, mode, job_id, names

    @app.route("/api/ai", methods=["POST"])
    def ai_action():
        parsed = _resolve_ai_input(request)
        if isinstance(parsed, tuple) and len(parsed) == 2:
            return parsed  # error response

        full_text, mode, job_id, names = parsed
        if not full_text:
            return jsonify({"error": "No text"}), 400
        if not ai_service.is_configured():
            return (
                jsonify(
                    {
                        "error": "AI endpoint not configured",
                        "hint": ai_service.get_config_hint(),
                    }
                ),
                503,
            )
        try:
            reply = ai_service.process(full_text, mode, names=names)
            if job_id:
                job_store.add_ai_result(job_id, mode, reply)
            return jsonify({"result": reply, "text": reply})
        except Exception as exc:
            return jsonify(*_handle_ai_errors(exc))

    @app.route("/api/ai/stream", methods=["POST"])
    def ai_action_stream():
        parsed = _resolve_ai_input(request)
        if isinstance(parsed, tuple) and len(parsed) == 2:
            return parsed  # error response

        full_text, mode, job_id, names = parsed
        if not full_text:
            return jsonify({"error": "No text"}), 400
        if not ai_service.is_configured():
            return (
                jsonify(
                    {
                        "error": "AI endpoint not configured",
                        "hint": ai_service.get_config_hint(),
                    }
                ),
                503,
            )

        def generate():
            full_reply = ""
            ev = _make_cancel_event(job_id) if job_id else None
            try:
                for chunk in ai_service.process_stream(full_text, mode, names=names):
                    full_reply += chunk
                    yield _sse({"chunk": chunk})
                    if ev and ev.is_set():
                        yield _sse({"error": "cancelled by user"})
                        return

                if job_id:
                    job_store.add_ai_result(job_id, mode, full_reply)
                yield _sse({"done": True, "text": full_reply})

            except Exception as exc:
                error_payload, _ = _handle_ai_errors(exc)
                yield _sse(error_payload)
            finally:
                if job_id:
                    _cancel_events.pop(job_id, None)

        return Response(generate(), mimetype="text/event-stream")

    # ------------------------------------------------------------------
    # Transcription progress stream
    # ------------------------------------------------------------------

    @app.route("/api/transcribe/stream/<job_id>")
    def transcribe_stream(job_id):
        def generate():
            last_pct = -1
            last_text = ""
            try:
                while True:
                    progress = _get_progress(job_id)
                    if progress:
                        curr_pct = progress.get("pct", 0)
                        curr_text = progress.get("text_segment", "")
                        curr_stage = progress.get("stage")

                        if curr_pct != last_pct or curr_text != last_text:
                            last_pct = curr_pct
                            last_text = curr_text
                            yield _sse(progress)

                        if curr_stage in ("done", "error", "cancelled"):
                            break

                    if not job_store.get(job_id):
                        break

                    time.sleep(0.2)
            except GeneratorExit:
                pass

        return Response(generate(), mimetype="text/event-stream")

    # ------------------------------------------------------------------
    # Personal names
    # ------------------------------------------------------------------

    @app.route("/api/personal-names", methods=["GET"])
    def get_personal_names():
        return jsonify({"names": config.get_personal_names()})

    @app.route("/api/personal-names", methods=["POST"])
    def save_personal_names():
        data = request.get_json(force=True)
        names = data.get("names", [])
        if not isinstance(names, list):
            return jsonify({"error": "names must be a list"}), 400
        names = [n.strip() for n in names if isinstance(n, str) and n.strip()]
        if config.save_personal_names(names):
            return jsonify({"names": names})
        return jsonify({"error": "Failed to save names"}), 500

    # ------------------------------------------------------------------
    # Info / debug
    # ------------------------------------------------------------------

    @app.route("/api/info")
    def info():
        return jsonify(
            {
                "whisper_bin": config.WHISPER_BIN,
                "whisper_model": config.WHISPER_MODEL,
                "is_macos": config.IS_MACOS,
                "ai_enabled": ai_service.is_configured(),
                "ai_model": config.AI_MODEL if ai_service.is_configured() else None,
            }
        )

    @app.route("/api/debug/<job_id>")
    def debug(job_id):
        j = job_store.get(job_id)
        if not j:
            return jsonify({"error": "Not found"}), 404
        return jsonify(
            {
                "id": j["id"],
                "status": j["status"],
                "error": j.get("error"),
                "debug_log": j.get("debug_log", "(no log yet)"),
                "cmd": (
                    f"{config.WHISPER_BIN} --model {config.WHISPER_MODEL}"
                    f" --file <wav> --output-txt"
                    f" --threads {max(4, os.cpu_count() or 4)} --vad"
                ),
            }
        )
