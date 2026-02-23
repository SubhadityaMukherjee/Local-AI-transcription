"""API routes for Whisper Studio."""

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

from flask import Flask, jsonify, render_template, request, send_file, Response


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
            return (
                jsonify(
                    {"error": f"Unsupported type: .{config.file_extension(f.filename)}"}
                ),
                400,
            )

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

        append_to = request.form.get("append_to")
        mime = blob.mimetype or blob.content_type or ""
        ext = "mp4" if "mp4" in mime else "ogg" if "ogg" in mime else "webm"

        if append_to:
            return _handle_append(blob, append_to, ext)

        # Normal recording mode
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
                error=f"Recording too small ({size} bytes) — was the mic captured?",
            )
            return jsonify({"job_id": job["id"]})

        _start_job(job["id"], dest)
        return jsonify({"job_id": job["id"]})

    def _handle_append(blob, append_to: str, ext: str):
        """Append new audio to an existing recording job and re-transcribe."""
        existing_job = job_store.get(append_to)
        if not existing_job:
            return jsonify({"error": "Job not found for append"}), 404

        recording_path = None
        if existing_job.get("recording"):
            p = config.BASE_DIR / existing_job["recording"]
            if p.exists():
                recording_path = p

        if not recording_path:
            return (
                jsonify(
                    {
                        "error": "No recording found for append",
                        "hint": "This job was created from an uploaded file without a saved recording.",
                    }
                ),
                404,
            )

        ts = time.strftime("%Y%m%d_%H%M%S")
        new_saved = config.RECORDINGS_DIR / f"append_{ts}.{ext}"
        blob.save(new_saved)

        if new_saved.stat().st_size < 1000:
            new_saved.unlink(missing_ok=True)
            return jsonify({"error": "Appended recording too small"}), 400

        combined_path = config.RECORDINGS_DIR / f"combined_{ts}.{ext}"
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(recording_path),
                    "-i",
                    str(new_saved),
                    "-filter_complex",
                    "[0:a][1:a]concat=n=2:v=0:a=1[a]",
                    "-map",
                    "[a]",
                    str(combined_path),
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 or not combined_path.exists():
                print(
                    f"[record] ffmpeg concat failed, using new recording only: {result.stderr[:500]}"
                )
                shutil.copy2(new_saved, combined_path)
            else:
                print("[record] appended audio to existing recording")
        except Exception as e:
            print(f"[record] concat error: {e}, using new recording only")
            shutil.copy2(new_saved, combined_path)
        finally:
            new_saved.unlink(missing_ok=True)
            # Clean up the old combined recording now that we have a new one
            if recording_path != config.BASE_DIR / existing_job.get("recording", ""):
                recording_path.unlink(missing_ok=True)

        new_recording = str(combined_path.relative_to(config.BASE_DIR))
        job_store.update(
            append_to,
            recording=new_recording,
            transcript=None,
            status="queued",
            progress=0,
            error=None,
        )

        dest = config.UPLOAD_DIR / f"{append_to}.{ext}"
        shutil.copy2(combined_path, dest)
        _start_job(append_to, dest)
        return jsonify({"job_id": append_to})

    @app.route("/api/status/<job_id>")
    def status(job_id):
        j = job_store.get(job_id)
        return jsonify(j) if j else (jsonify({"error": "Not found"}), 404)

    @app.route("/api/jobs")
    def list_jobs():
        return jsonify(job_store.list_all())

    @app.route("/api/ai", methods=["POST"])
    def ai_action():
        audio_file = request.files.get("audio")

        if audio_file:
            text = (request.form.get("text") or "").strip()
            mode = request.form.get("mode", "edit")
            job_id = request.form.get("job_id")

            # Preserve the actual file extension so ffmpeg/whisper can decode it
            mime = audio_file.mimetype or audio_file.content_type or ""
            suffix = ".mp4" if "mp4" in mime else ".ogg" if "ogg" in mime else ".webm"
            audio_path = (
                Path(tempfile.gettempdir()) / f"voice_edit_{uuid.uuid4()}{suffix}"
            )
            audio_file.save(str(audio_path))

            try:
                job_id_temp = f"voice_edit_{uuid.uuid4()}"
                vstatus, transcript_result = transcription_service.process(
                    audio_path, job_id_temp, lambda p: None
                )
                if vstatus != "done":
                    return (
                        jsonify(
                            {
                                "error": f"Failed to transcribe audio: {transcript_result}"
                            }
                        ),
                        500,
                    )

                full_text = (
                    f"Original text:\n{text}\n\nVoice command:\n{transcript_result}"
                )
                personal_names = None
            except Exception as e:
                return jsonify({"error": f"Failed to process audio: {e}"}), 500
        else:
            data = request.get_json(force=True)
            full_text = (data.get("text") or "").strip()
            mode = data.get("mode", "summarize")
            job_id = data.get("job_id")
            personal_names = data.get("personal_names")

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
            reply = ai_service.process(full_text, mode, names=personal_names)
            if job_id:
                job_store.add_ai_result(job_id, mode, reply)
            return jsonify({"result": reply, "text": reply})

        except urlerr.HTTPError as e:
            body = e.read().decode(errors="replace")
            print(f"[ai] HTTP {e.code}: {body}")
            return (
                jsonify({"error": f"Ollama returned HTTP {e.code}", "detail": body}),
                503,
            )

        except urlerr.URLError as e:
            print(f"[ai] Cannot reach Ollama: {e.reason}")
            return (
                jsonify(
                    {
                        "error": "Cannot reach Ollama — is it running?",
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

    @app.route("/api/ai/stream", methods=["POST"])
    def ai_action_stream():
        """Streaming AI endpoint for real-time response display."""
        audio_file = request.files.get("audio")

        if audio_file:
            text = (request.form.get("text") or "").strip()
            mode = request.form.get("mode", "edit")
            job_id = request.form.get("job_id")

            # Preserve the actual file extension so ffmpeg/whisper can decode it
            mime = audio_file.mimetype or audio_file.content_type or ""
            suffix = ".mp4" if "mp4" in mime else ".ogg" if "ogg" in mime else ".webm"
            audio_path = (
                Path(tempfile.gettempdir()) / f"voice_edit_{uuid.uuid4()}{suffix}"
            )
            audio_file.save(str(audio_path))

            try:
                job_id_temp = f"voice_edit_{uuid.uuid4()}"
                vstatus, transcript_result = transcription_service.process(
                    audio_path, job_id_temp, lambda p: None
                )
                if vstatus != "done":
                    return (
                        jsonify(
                            {
                                "error": f"Failed to transcribe audio: {transcript_result}"
                            }
                        ),
                        500,
                    )

                full_text = (
                    f"Original text:\n{text}\n\nVoice command:\n{transcript_result}"
                )
                personal_names = None
            except Exception as e:
                return jsonify({"error": f"Failed to process audio: {e}"}), 500
        else:
            data = request.get_json(force=True)
            full_text = (data.get("text") or "").strip()
            mode = data.get("mode", "summarize")
            job_id = data.get("job_id")
            personal_names = data.get("personal_names")

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
            """Generator that yields streaming AI response as SSE."""
            full_reply = ""
            try:
                for chunk in ai_service.process_stream(
                    full_text, mode, names=personal_names
                ):
                    full_reply += chunk
                    # Yield as SSE format
                    yield f"data: {json.dumps({'chunk': chunk})}\n\n"

                # Save result to job store after completion
                if job_id:
                    job_store.add_ai_result(job_id, mode, full_reply)

                # Send completion signal
                yield f"data: {json.dumps({'done': True, 'text': full_reply})}\n\n"

            except urlerr.HTTPError as e:
                body = e.read().decode(errors="replace")
                print(f"[ai stream] HTTP {e.code}: {body}")
                yield f"data: {json.dumps({'error': f'Ollama returned HTTP {e.code}', 'detail': body})}\n\n"

            except urlerr.URLError as e:
                print(f"[ai stream] Cannot reach Ollama: {e.reason}")
                yield f"data: {json.dumps({'error': 'Cannot reach Ollama', 'detail': str(e.reason)})}\n\n"

            except Exception as e:
                import traceback

                print(f"[ai stream] Unexpected error: {traceback.format_exc()}")
                yield f"data: {json.dumps({'error': f'AI request failed: {e}'})}\n\n"

        return Response(generate(), mimetype="text/event-stream")

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
                    f" --file <wav> --output-txt --threads {max(4, os.cpu_count() or 4)} --vad"
                ),
            }
        )

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

    # Store for transcription progress that can be accessed by streaming endpoint
    _transcription_progress: dict = {}
    _progress_lock = threading.Lock()

    def _start_job(job_id: str, src: Path):
        """Start processing job in background thread."""

        def run():
            # Mark as converting immediately
            job_store.update(job_id, status="converting", progress=5)
            
            # Initialize progress tracking
            with _progress_lock:
                _transcription_progress[job_id] = {
                    "stage": "converting",
                    "pct": 5,
                    "message": "Converting audio file...",
                    "start_time": time.time(),
                }

            def on_progress(pct):
                # Once we pass 30%, we are transcribing
                status = "transcribing" if pct >= 30 else "converting"
                job_store.update(job_id, progress=pct, status=status)
                
                # Store detailed progress
                progress_data = {
                    "stage": status,
                    "pct": pct,
                    "message": f"{'Converting' if status == 'converting' else 'Transcribing'}: {pct}%",
                    "start_time": _transcription_progress.get(job_id, {}).get("start_time", time.time()),
                }
                
                with _progress_lock:
                    _transcription_progress[job_id] = progress_data

            vstatus, result = transcription_service.process(src, job_id, on_progress)

            if vstatus == "done":
                job_store.update(job_id, status="done", progress=100, transcript=result)
                with _progress_lock:
                    _transcription_progress[job_id] = {
                        "stage": "done",
                        "pct": 100,
                        "message": "Transcription complete!",
                        "start_time": _transcription_progress.get(job_id, {}).get("start_time", time.time()),
                    }
            else:
                job_store.update(job_id, status="error", error=result)
                with _progress_lock:
                    _transcription_progress[job_id] = {
                        "stage": "error",
                        "pct": 0,
                        "message": f"Error: {result}",
                        "start_time": _transcription_progress.get(job_id, {}).get("start_time", time.time()),
                    }

        threading.Thread(target=run, daemon=True).start()

    @app.route("/api/transcribe/stream/<job_id>")
    def transcribe_stream(job_id):
        """Streaming endpoint for transcription progress."""
        import queue
        import uuid
        
        # Create a queue for this stream
        stream_id = str(uuid.uuid4())
        progress_queue = queue.Queue()
        
        # Register this stream
        if not hasattr(_start_job, 'streams'):
            _start_job.streams = {}
        _start_job.streams[stream_id] = progress_queue
        
        def generate():
            try:
                last_pct = -1
                while True:
                    with _progress_lock:
                        progress = _transcription_progress.get(job_id)
                    
                    if progress:
                        pct = progress.get("pct", 0)
                        # Only send update if progress changed
                        if pct != last_pct:
                            last_pct = pct
                            yield f"data: {json.dumps(progress)}\n\n"
                            
                            # Stop when done or error
                            if progress.get("stage") in ("done", "error"):
                                break
                    
                    # Check if job still exists
                    job = job_store.get(job_id)
                    if not job or job.get("status") in ("done", "error"):
                        break
                    
                    time.sleep(0.5)
            finally:
                # Cleanup
                if hasattr(_start_job, 'streams'):
                    _start_job.streams.pop(stream_id, None)
        
        return Response(generate(), mimetype="text/event-stream")
