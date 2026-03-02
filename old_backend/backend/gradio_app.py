"""
Whisper Studio — Gradio UI
Drop-in replacement for the Flask routes layer.
Supports streaming transcription progress and streaming AI output.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Generator

import gradio as gr

from backend.routes import (
    _cancel_events,
    _enqueue,
    _get_progress,
    _make_cancel_event,
    _save_recording_gradio,
    _start_queue_worker,
    _transcribe_audio_blob,
)


def build_app(config, job_store, transcription_service, ai_service) -> gr.Blocks:
    """Build and return the Gradio Blocks application."""

    _start_queue_worker(job_store, transcription_service)

    # ------------------------------------------------------------------ #
    # Jobs tab                                                            #
    # ------------------------------------------------------------------ #

    def _jobs_table() -> list[list]:
        rows = []
        for j in job_store.list_all():
            tx = j.get("transcript") or ""
            rows.append(
                [
                    j["id"][:8],
                    j.get("filename", "—"),
                    j.get("status", "—"),
                    f"{j.get('progress', 0)}%",
                    tx[:80] + ("…" if len(tx) > 80 else ""),
                ]
            )
        return rows

    def refresh_jobs():
        return _jobs_table()

    def get_transcript(job_id: str) -> str:
        j = job_store.get(job_id.strip())
        return j.get("transcript") or "(no transcript yet)" if j else "Job not found."

    def cancel_job(job_id: str) -> str:
        ev = _cancel_events.get(job_id.strip())
        return (
            f"Cancellation requested for {job_id[:8]}."
            if ev and not ev.set()
            else "No active job found with that ID."
        )

    def delete_job(job_id: str):
        ok = job_store.delete(job_id.strip())
        msg = f"Deleted {job_id[:8]}." if ok else "Job not found."
        return msg, _jobs_table()

    def clear_completed():
        n = job_store.clear_completed()
        return f"Cleared {n} completed job(s).", _jobs_table()

    # ------------------------------------------------------------------ #
    # Transcribe tab — streaming progress                                 #
    # ------------------------------------------------------------------ #

    def _stream_job_progress(job_id: str) -> Generator:
        """Yield (status, job_id, stage, transcript) while the job runs."""
        yield f"Queued — job: {job_id[:8]}", job_id, "queued", ""

        last_pct, last_stage = -1, ""

        while True:
            progress = _get_progress(job_id)
            j = job_store.get(job_id)

            if progress:
                pct = progress.get("pct", 0)
                stage = progress.get("stage", "")
                msg = progress.get("message", "")
                seg = progress.get("text_segment", "")

                if pct != last_pct or stage != last_stage:
                    last_pct, last_stage = pct, stage
                    transcript = (j.get("transcript") if j else None) or seg or ""
                    yield f"{msg} ({pct}%)", job_id, stage, transcript

                if stage in ("done", "error", "cancelled"):
                    break

            elif j and j.get("status") in ("done", "error", "cancelled"):
                yield j["status"], job_id, j["status"], j.get("transcript") or ""
                break

            time.sleep(0.25)

    def transcribe_file(audio_path: str) -> Generator:
        if not audio_path:
            yield "No file provided.", "", "", ""
            return

        p = Path(audio_path)
        if not config.allowed_file(p.name):
            yield f"Unsupported file type: {p.suffix}", "", "", ""
            return

        job = job_store.create(p.name)
        dest = config.UPLOAD_DIR / f"{job['id']}_{p.name}"
        shutil.copy2(p, dest)
        _enqueue(job["id"], dest)
        yield from _stream_job_progress(job["id"])

    def transcribe_recording(audio_path: str) -> Generator:
        if not audio_path:
            yield "No recording provided.", "", "", ""
            return

        p = Path(audio_path)
        ts = time.strftime("%Y%m%d_%H%M%S")
        job = job_store.create(f"recording_{ts}{p.suffix}")

        saved, dest = _save_recording_gradio(config, p, job["id"], p.suffix.lstrip("."))
        size = saved.stat().st_size

        if size < 1000:
            saved.unlink(missing_ok=True)
            dest.unlink(missing_ok=True)
            job_store.update(
                job["id"], status="error", error=f"Recording too small ({size} bytes)"
            )
            yield f"Error: recording too small ({size} B).", job["id"], "error", ""
            return

        job_store.update(job["id"], recording=str(saved.relative_to(config.BASE_DIR)))
        _enqueue(job["id"], dest)
        yield from _stream_job_progress(job["id"])

    # ------------------------------------------------------------------ #
    # AI tab — streaming output                                           #
    # ------------------------------------------------------------------ #

    def _ai_check() -> str | None:
        if not ai_service.is_configured():
            return f"⚠️ AI not configured. {ai_service.get_config_hint()}"
        return None

    def ai_stream_text(text: str, mode: str, job_id: str, names_raw: str) -> Generator:
        if err := _ai_check():
            yield err
            return
        if not text.strip():
            yield "No text provided."
            return

        names = [n.strip() for n in names_raw.split(",") if n.strip()] or None
        job_id = job_id.strip()
        ev = _make_cancel_event(job_id) if job_id else None
        full_reply = ""

        try:
            for chunk in ai_service.process_stream(text.strip(), mode, names=names):
                full_reply += chunk
                yield full_reply
                if ev and ev.is_set():
                    yield full_reply + "\n\n⛔ Cancelled."
                    return
            if job_id:
                job_store.add_ai_result(job_id, mode, full_reply)
        except Exception as exc:
            yield f"Error: {exc}"
        finally:
            if job_id:
                _cancel_events.pop(job_id, None)

    def ai_stream_voice(
        audio_path: str, original_text: str, mode: str, job_id: str
    ) -> Generator:
        if err := _ai_check():
            yield err
            return
        if not audio_path:
            yield "No audio provided."
            return

        yield "🎙️ Transcribing voice command…"

        class _Blob:
            def __init__(self, path):
                self._path = Path(path)
                self.mimetype = ""
                self.content_type = self._path.suffix.lstrip(".")

            def save(self, dest):
                shutil.copy2(self._path, dest)

        vstatus, transcript = _transcribe_audio_blob(
            _Blob(audio_path), config, transcription_service
        )
        if vstatus != "done":
            yield f"Transcription failed: {transcript}"
            return

        yield f"✅ Voice: *{transcript}*\n\n"
        combined = f"Original text:\n{original_text}\n\nVoice command:\n{transcript}"
        job_id = job_id.strip()
        full_reply = ""

        try:
            for chunk in ai_service.process_stream(combined, mode):
                full_reply += chunk
                yield full_reply
            if job_id:
                job_store.add_ai_result(job_id, mode, full_reply)
        except Exception as exc:
            yield f"Error: {exc}"

    def cancel_ai(job_id: str) -> str:
        ev = _cancel_events.get(job_id.strip())
        if ev:
            ev.set()
            return "Cancellation requested."
        return "No active AI stream for that job ID."

    # ------------------------------------------------------------------ #
    # Personal names                                                      #
    # ------------------------------------------------------------------ #

    def load_names() -> str:
        return ", ".join(config.get_personal_names())

    def save_names(raw: str) -> str:
        names = [n.strip() for n in raw.split(",") if n.strip()]
        return (
            f"✅ Saved {len(names)} name(s)."
            if config.save_personal_names(names)
            else "❌ Failed to save."
        )

    # ------------------------------------------------------------------ #
    # Info                                                                #
    # ------------------------------------------------------------------ #

    def get_info() -> str:
        lines = [
            f"**Whisper binary:** `{config.WHISPER_BIN or '⚠ not found — run ./setup.sh'}`",
            f"**Whisper model:** `{config.WHISPER_MODEL or '⚠ not found — run ./setup.sh'}`",
            f"**Platform:** {'macOS' if config.IS_MACOS else 'Linux / Other'}",
            f"**AI enabled:** {ai_service.is_configured()}",
        ]
        if ai_service.is_configured():
            lines.append(f"**AI model:** `{config.AI_MODEL}`")
        return "\n\n".join(lines)

    # ------------------------------------------------------------------ #
    # Layout                                                              #
    # ------------------------------------------------------------------ #

    ai_modes = ai_service.available_modes()

    with gr.Blocks(title="Whisper Studio", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 🎙️ Whisper Studio")

        with gr.Tabs():

            # ── TRANSCRIBE ──────────────────────────────────────────────
            with gr.TabItem("📤 Transcribe"):
                gr.Markdown("Upload a file or record from your microphone.")

                with gr.Row():
                    with gr.Column():
                        upload_audio = gr.Audio(
                            label="Upload audio / video",
                            type="filepath",
                            sources=["upload"],
                        )
                        upload_btn = gr.Button("Transcribe file", variant="primary")
                    with gr.Column():
                        record_audio = gr.Audio(
                            label="Record from mic",
                            type="filepath",
                            sources=["microphone"],
                        )
                        record_btn = gr.Button(
                            "Transcribe recording", variant="primary"
                        )

                with gr.Row():
                    tx_status = gr.Textbox(label="Status", interactive=False, scale=3)
                    tx_job_id = gr.Textbox(label="Job ID", interactive=False, scale=2)
                    tx_stage = gr.Textbox(visible=False)  # hidden, used internally

                transcript_box = gr.Textbox(
                    label="Transcript (updates live)",
                    lines=10,
                    interactive=False,
                )

                upload_btn.click(
                    transcribe_file,
                    inputs=upload_audio,
                    outputs=[tx_status, tx_job_id, tx_stage, transcript_box],
                )
                record_btn.click(
                    transcribe_recording,
                    inputs=record_audio,
                    outputs=[tx_status, tx_job_id, tx_stage, transcript_box],
                )

            # ── AI ACTIONS ──────────────────────────────────────────────
            with gr.TabItem("🤖 AI Actions"):
                gr.Markdown(
                    "Apply an AI mode to text or a voice command. Output streams in real-time."
                )

                ai_mode_dd = gr.Dropdown(
                    choices=ai_modes,
                    value=ai_modes[0] if ai_modes else None,
                    label="AI Mode",
                )

                with gr.Tabs():
                    with gr.TabItem("✏️ Text input"):
                        ai_text_in = gr.Textbox(
                            label="Text",
                            lines=6,
                            placeholder="Paste transcript or any text…",
                        )
                        with gr.Row():
                            ai_job_id_t = gr.Textbox(
                                label="Job ID (optional — saves result)",
                                placeholder="paste job ID",
                                scale=3,
                            )
                            ai_names_t = gr.Textbox(
                                label="Personal names hint (comma-separated)",
                                scale=3,
                            )
                        with gr.Row():
                            ai_run_btn = gr.Button("▶ Run", variant="primary")
                            ai_cancel_btn = gr.Button("⛔ Cancel")

                    with gr.TabItem("🎙️ Voice command"):
                        ai_audio_in = gr.Audio(
                            label="Voice command",
                            type="filepath",
                            sources=["microphone", "upload"],
                        )
                        ai_orig_text = gr.Textbox(
                            label="Original text (context for the command)",
                            lines=4,
                        )
                        with gr.Row():
                            ai_job_id_v = gr.Textbox(label="Job ID (optional)", scale=3)
                            ai_voice_btn = gr.Button("▶ Run", variant="primary")
                            ai_voice_cancel = gr.Button("⛔ Cancel")

                ai_result = gr.Textbox(
                    label="AI Result (streams live)", lines=12, interactive=False
                )
                _cancel_status = gr.Textbox(visible=False)

                ai_run_btn.click(
                    ai_stream_text,
                    inputs=[ai_text_in, ai_mode_dd, ai_job_id_t, ai_names_t],
                    outputs=ai_result,
                )
                ai_cancel_btn.click(
                    cancel_ai, inputs=ai_job_id_t, outputs=_cancel_status
                )

                ai_voice_btn.click(
                    ai_stream_voice,
                    inputs=[ai_audio_in, ai_orig_text, ai_mode_dd, ai_job_id_v],
                    outputs=ai_result,
                )
                ai_voice_cancel.click(
                    cancel_ai, inputs=ai_job_id_v, outputs=_cancel_status
                )

            # ── JOBS ────────────────────────────────────────────────────
            with gr.TabItem("📋 Jobs"):
                jobs_table = gr.Dataframe(
                    headers=[
                        "ID (short)",
                        "Filename",
                        "Status",
                        "Progress",
                        "Transcript preview",
                    ],
                    datatype=["str", "str", "str", "str", "str"],
                    interactive=False,
                    wrap=True,
                )
                with gr.Row():
                    refresh_btn = gr.Button("🔄 Refresh")
                    clear_btn = gr.Button("🗑️ Clear completed")
                jobs_msg = gr.Textbox(label="", interactive=False)

                gr.Markdown("---")
                gr.Markdown("**Job actions** — enter the full job ID")
                with gr.Row():
                    job_id_box = gr.Textbox(label="Full Job ID", scale=4)
                    get_tx_btn = gr.Button("📄 Transcript")
                    cancel_job_btn = gr.Button("⛔ Cancel")
                    delete_job_btn = gr.Button("❌ Delete")
                job_transcript = gr.Textbox(
                    label="Transcript", lines=8, interactive=False
                )

                refresh_btn.click(refresh_jobs, outputs=jobs_table)
                clear_btn.click(clear_completed, outputs=[jobs_msg, jobs_table])
                get_tx_btn.click(
                    get_transcript, inputs=job_id_box, outputs=job_transcript
                )
                cancel_job_btn.click(cancel_job, inputs=job_id_box, outputs=jobs_msg)
                delete_job_btn.click(
                    delete_job, inputs=job_id_box, outputs=[jobs_msg, jobs_table]
                )
                demo.load(refresh_jobs, outputs=jobs_table)

            # ── PERSONAL NAMES ──────────────────────────────────────────
            with gr.TabItem("🪪 Personal Names"):
                gr.Markdown(
                    "Names are passed to the AI as spelling hints, "
                    "improving accuracy for uncommon proper nouns."
                )
                names_box = gr.Textbox(
                    label="Names (comma-separated)",
                    lines=4,
                    placeholder="Alice, Bob, Dr. Smith…",
                )
                with gr.Row():
                    load_names_btn = gr.Button("↩ Load saved")
                    save_names_btn = gr.Button("💾 Save", variant="primary")
                names_msg = gr.Textbox(label="", interactive=False)

                load_names_btn.click(load_names, outputs=names_box)
                save_names_btn.click(save_names, inputs=names_box, outputs=names_msg)
                demo.load(load_names, outputs=names_box)

            # ── INFO ────────────────────────────────────────────────────
            with gr.TabItem("ℹ️ Info"):
                info_md = gr.Markdown()
                demo.load(get_info, outputs=info_md)

    return demo
