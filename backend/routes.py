"""API routes for Whisper Studio."""

import shutil
import threading
import time
from pathlib import Path
from urllib import error as urlerr

from flask import Flask, request, jsonify, render_template, send_file


def register_routes(app: Flask, config, job_store, transcription_service, ai_service):
    """Register all API routes."""

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/upload", methods=["POST"])
    def upload():
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"error": "No file"}), 400
        if not config.allowed_file(f.filename):
            return jsonify({"error": f"Unsupported type: .{config.file_extension(f.filename)}"}), 400

        job = job_store.create(f.filename)
        dest = config.UPLOAD_DIR / f"{job['id']}_{f.filename}"
        f.save(dest)
        _start_job(job["id"], dest)
        return jsonify({"job_id": job["id"]})

    @app.route("/api/record", methods=["POST"])
    def record():
        blob = request.files.get("audio")
        if not blob:
            print("[record] ERROR: no audio field in request")
            return jsonify({"error": "No audio"}), 400

        mime = blob.mimetype or blob.content_type or ""
        ext = "mp4" if "mp4" in mime else "ogg" if "ogg" in mime else "webm"

        ts = time.strftime("%Y%m%d_%H%M%S")
        job = job_store.create(f"recording_{ts}.{ext}")
        filename = f'{ts}_{job["id"][:8]}.{ext}'

        saved = config.RECORDINGS_DIR / filename
        blob.save(saved)
        size = saved.stat().st_size
        print(f"[record] saved → recordings/{filename} ({size} bytes, mime={mime})")

        dest = config.UPLOAD_DIR / f"{job['id']}.{ext}"
        shutil.copy2(saved, dest)

        job_store.update(job["id"], recording=str(saved.relative_to(config.BASE_DIR)))

        if size < 1000:
            dest.unlink(missing_ok=True)
            job_store.update(
                job["id"],
                status="error",
                error=f"Recording too small ({size} bytes) — was the mic captured?"
            )
            return jsonify({"job_id": job["id"]})

        _start_job(job["id"], dest)
        return jsonify({"job_id": job["id"]})

    @app.route("/api/status/<job_id>")
    def status(job_id):
        j = job_store.get(job_id)
        return jsonify(j) if j else (jsonify({"error": "Not found"}), 404)

    @app.route("/api/jobs")
    def list_jobs():
        return jsonify(job_store.list_all())

    @app.route("/api/ai", methods=["POST"])
    def ai_action():
        data = request.get_json(force=True)
        text = (data.get("text") or "").strip()
        mode = data.get("mode", "summarize")
        job_id = data.get("job_id")

        if not text:
            return jsonify({"error": "No text"}), 400
        if not ai_service.is_configured():
            return jsonify({
                "error": "AI endpoint not configured",
                "hint": ai_service.get_config_hint(),
            }), 503

        try:
            reply = ai_service.process(text, mode)

            if job_id:
                job_store.add_ai_result(job_id, mode, reply)

            return jsonify({"result": reply})

        except urlerr.HTTPError as e:
            body = e.read().decode(errors="replace")
            print(f"[ai] HTTP {e.code}: {body}")
            return jsonify({"error": f"Ollama returned HTTP {e.code}", "detail": body}), 503

        except urlerr.URLError as e:
            print(f"[ai] Cannot reach Ollama: {e.reason}")
            return jsonify({
                "error": f"Cannot reach Ollama — is it running?",
                "detail": str(e.reason),
                "hint": "Run: ollama serve",
            }), 503

        except Exception as e:
            import traceback
            print(f"[ai] Unexpected error: {traceback.format_exc()}")
            return jsonify({"error": f"AI request failed: {e}"}), 503

    @app.route("/api/jobs/<job_id>/download")
    def download_recording(job_id):
        j = job_store.get(job_id)
        if not j or not j.get("recording"):
            return jsonify({"error": "No recording saved for this job"}), 404
        path = config.BASE_DIR / j["recording"]
        if not path.exists():
            return jsonify({"error": "Recording file not found on disk"}), 404
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.route("/api/info")
    def info():
        return jsonify({
            "whisper_bin": config.WHISPER_BIN,
            "whisper_model": config.WHISPER_MODEL,
            "is_macos": config.IS_MACOS,
            "ai_enabled": ai_service.is_configured(),
            "ai_model": config.AI_MODEL if ai_service.is_configured() else None,
        })

    @app.route("/api/debug/<job_id>")
    def debug(job_id):
        j = job_store.get(job_id)
        if not j:
            return jsonify({"error": "Not found"}), 404
        return jsonify({
            "id": j["id"],
            "status": j["status"],
            "error": j.get("error"),
            "debug_log": j.get("debug_log", "(no log yet)"),
            "cmd": f"{config.WHISPER_BIN} --model {config.WHISPER_MODEL} --file <wav> --output-txt --threads {max(4, __import__('os').cpu_count() or 4)}",
        })

    @app.route("/api/jobs/<job_id>/ai/<int:idx>", methods=["DELETE"])
    def delete_ai_result(job_id, idx):
        if job_store.delete_ai_result(job_id, idx):
            return jsonify({"ai_results": job_store.get(job_id).get("ai_results", [])})
        return jsonify({"error": "Not found"}), 404

    @app.route("/api/jobs/<job_id>", methods=["DELETE"])
    def delete_job(job_id):
        if job_store.delete(job_id):
            return jsonify({"deleted": job_id})
        return jsonify({"error": "Not found"}), 404

    @app.route("/api/jobs/clear", methods=["POST"])
    def clear_jobs():
        cleared = job_store.clear_completed()
        return jsonify({"cleared": cleared})

    def _start_job(job_id: str, src: Path):
        """Start processing job in background."""
        def run():
            def on_progress(pct):
                job_store.update(job_id, progress=pct)

            status, result = transcription_service.process(src, job_id, on_progress)

            if status == "done":
                job_store.update(job_id, status="done", progress=100, transcript=result)
            else:
                job_store.update(job_id, status="error", error=result)

        threading.Thread(target=run, daemon=True).start()
