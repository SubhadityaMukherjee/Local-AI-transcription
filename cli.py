#!/usr/bin/env python3

import logging
import os
import subprocess
import sys
import threading
import time
import uuid
import tempfile
from pathlib import Path
import functools
import click
import shutil
from dotenv import load_dotenv

load_dotenv()


# ── Timing decorator ──────────────────────────────────────────────────────────
def timed(func):
    """Decorator to log the execution time of a function."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            end = time.perf_counter()
            logger = logging.getLogger("whisper_cli.ai_service")
            logger.info("⏱ %s executed in %.3f seconds", func.__name__, end - start)

    return wrapper


# ── Logging setup ──────────────────────────────────────────────────────────────


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")
    # Silence noisy third-party loggers unless in verbose mode
    if not verbose:
        for noisy in ("urllib3", "httpx", "httpcore", "ollama"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


logger = logging.getLogger("whisper_cli")

# ── Lazy imports (so --help is instant) ───────────────────────────────────────


def _load_services(use_db: bool = False):
    """Import and wire up all services."""
    from backend.ai_service import AIService
    from backend.config import Config
    from backend.job_store import SQLJobStore
    from backend.transcription_service import TranscriptionService

    cfg = Config()

    store = SQLJobStore(cfg)
    logger.info(
        "Using SQLAlchemy job store (DATABASE_URL=%s)",
        os.environ.get("DATABASE_URL", "sqlite:///whisper.db"),
    )

    ai = AIService(cfg)
    ts = TranscriptionService(cfg)
    return cfg, store, ai, ts


# ── Recording helper ───────────────────────────────────────────────────────────


def record_audio(output_path: Path, duration: int | None = None) -> Path:
    """
    Record audio from the default microphone.

    Uses sounddevice + soundfile when available, falls back to arecord (Linux)
    or afplay (macOS). Returns the path to the recorded WAV file.
    """
    logger.info("Starting microphone recording → %s", output_path)

    try:
        import sounddevice as sd  # type: ignore
        import soundfile as sf  # type: ignore

        samplerate = 16_000
        channels = 1

        if duration:
            click.echo(f"🎙  Recording for {duration}s…")
            logger.debug("Fixed-duration record: %ds @ %dHz", duration, samplerate)
            audio = sd.rec(
                int(duration * samplerate),
                samplerate=samplerate,
                channels=channels,
                dtype="int16",
            )
            sd.wait()
        else:
            click.echo("🎙  Recording… Press ENTER to stop.")
            logger.debug("Open-ended record @ %dHz (press ENTER to stop)", samplerate)
            frames = []
            stop = threading.Event()

            def _capture():
                with sd.InputStream(
                    samplerate=samplerate, channels=channels, dtype="int16"
                ) as stream:
                    while not stop.is_set():
                        data, _ = stream.read(1024)
                        frames.append(data.copy())

            t = threading.Thread(target=_capture, daemon=True)
            t.start()
            input()  # Wait for ENTER
            stop.set()
            t.join()

            import numpy as np  # type: ignore

            audio = np.concatenate(frames, axis=0)

        sf.write(str(output_path), audio, samplerate, subtype="PCM_16")
        logger.info("Recording saved (%d samples)", len(audio))
        click.echo(f"✅ Saved recording to {output_path}")
        return output_path

    except ImportError:
        logger.warning(
            "sounddevice/soundfile not found — falling back to system recorder"
        )

    # ── System fallback ────────────────────────────────────────────────────────
    import platform
    import subprocess

    system = platform.system()
    if system == "Linux":
        cmd = ["arecord", "-f", "S16_LE", "-r", "16000", "-c", "1"]
        if duration:
            cmd += ["-d", str(duration)]
        cmd.append(str(output_path))
        click.echo(
            "🎙  Recording via arecord… (Ctrl-C to stop)"
            if not duration
            else f"🎙  Recording for {duration}s via arecord…"
        )
        logger.debug("arecord command: %s", " ".join(cmd))
    elif system == "Darwin":
        # sox is the common CLI recorder on macOS
        cmd = ["sox", "-d", "-r", "16000", "-c", "1", "-b", "16", str(output_path)]
        if duration:
            cmd += ["trim", "0", str(duration)]
        click.echo(
            "🎙  Recording via sox… (Ctrl-C to stop)"
            if not duration
            else f"🎙  Recording for {duration}s via sox…"
        )
        logger.debug("sox command: %s", " ".join(cmd))
    else:
        raise RuntimeError(
            "Automatic recording not supported on this platform.\n"
            "Install sounddevice + soundfile:  pip install sounddevice soundfile"
        )

    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        pass

    if not output_path.exists():
        raise RuntimeError("Recording failed — output file not found")

    logger.info("Recording saved to %s", output_path)
    click.echo(f"✅ Saved recording to {output_path}")
    return output_path


# ── Audio concatenation helper ───────────────────────────────────────────────

def _concat_audio_files(sources: list[Path], dst: Path):
    """Concatenate *Sources* into *dst* using ffmpeg.

    The files are joined in alphabetical order by filename.  A temporary
    file‑list is written and passed to ffmpeg's concat demuxer.  The output is
    encoded as 16kHz mono PCM WAV so it can be fed directly to the
    transcription pipeline.
    """
    # ensure deterministic ordering
    sources = sorted(sources, key=lambda p: p.name)
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
        for s in sources:
            tmp.write(f"file '{s.resolve()}'\n")
        list_path = tmp.name

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_path,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-sample_fmt",
        "s16",
        str(dst),
    ]
    logger.debug("ffmpeg concat command: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)
    try:
        os.unlink(list_path)
    except Exception:
        pass
# ── Progress display ───────────────────────────────────────────────────────────


def _progress_callback(store, job_id: str):
    """Return a progress callback with a responsive, dynamic, and smooth progress bar."""
    last_pct = [-1]

    def cb(value):
        # Determine progress percentage and message
        if isinstance(value, dict):
            pct = value.get("pct", last_pct[0])
            msg = value.get("message", "")
            seg = value.get("text_segment", "")
            if seg:
                click.echo(f"  💬 {seg}", err=True)
        else:
            pct = int(value)
            msg = f"{pct}%"

        if pct != last_pct[0]:
            last_pct[0] = pct

            # Dynamic terminal width
            terminal_width = shutil.get_terminal_size((80, 20)).columns

            # Reserve space for brackets, percentage, message, and padding
            reserved = 10 + len(msg)
            bar_len = max(10, terminal_width - reserved)

            # Smooth fill with Unicode block character
            filled_len = int(bar_len * pct / 100)
            bar = "█" * filled_len + "░" * (bar_len - filled_len)

            # Print the progress bar
            click.echo(
                f"\r  [{bar}] {pct:3d}% {msg.ljust(terminal_width - bar_len - 10)}",
                nl=False,
                err=True,
            )
            logger.debug("Progress %d%%: %s", pct, msg)

            # Update store
            store.update(job_id, progress=pct)

        if pct >= 100:
            click.echo("", err=True)  # newline after completion

    return cb


# ── CLI root ───────────────────────────────────────────────────────────────────


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.option(
    "--db",
    is_flag=True,
    envvar="USE_DB",
    help="Use SQLAlchemy job store instead of JSON (set DATABASE_URL env var)",
)
@click.pass_context
def cli(ctx, verbose, db):
    """
    🎙  Whisper Studio CLI — Transcribe & process audio with AI

    Record audio, transcribe with Whisper, and enhance with AI.
    """
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["db"] = db
    ctx.obj["verbose"] = verbose


# ── transcribe command ─────────────────────────────────────────────────────────


@cli.command()
# accept one or more files and move the mode to an option for clarity
@click.argument("files", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--mode", "-m",
    default="default",
    show_default=True,
    help="Optional AI mode to run after transcription (e.g., 'grammar', 'summarize')",
)
@click.option(
    "--names", default="", help="Comma-separated names to help spelling correction"
)
@click.option("--out", default=None, help="Write transcript to this file")
@click.pass_context
@timed

def transcribe(ctx, files, mode, names, out):
    """📁 Transcribe one or more audio/video files using Whisper.

    FILES: One or more paths to audio/video files. Supported formats include
    mp3, wav, flac, m4a, mp4, mkv, mov, etc.  When multiple files are given
    they are concatenated (sorted by filename) before transcription.

    --mode / -m: Optional AI mode to run after transcription (e.g., 'grammar',
    'summarize').
    """

    ai_mode = mode
    logger.info("=== transcribe command ===")
    cfg, store, ai, ts = _load_services(ctx.obj["db"])

    if not files:
        raise click.UsageError("At least one input file must be provided")

    sources = [Path(f) for f in files]
    sources.sort(key=lambda p: p.name)

    if len(sources) > 1:
        # each job is identified by the concatenated filenames for traceability
        job = store.create("+".join(p.name for p in sources))
        job_id = job["id"]
        concat_path = cfg.UPLOAD_DIR / f"{job_id}.concat.wav"
        concat_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Concatenating %d files into %s", len(sources), concat_path)
        _concat_audio_files(sources, concat_path)
        src = concat_path
    else:
        src = sources[0]
        job = store.create(src.name)
        job_id = job["id"]

    logger.info("Created job %s for '%s'", job_id[:8], src.name)

    store.update(job_id, status="processing")

    click.echo(f"\n📂 File   : {src}")
    click.echo(f"🆔 Job ID : {job_id}")
    click.echo("⚙️  Converting & transcribing…\n")

    cb = _progress_callback(store, job_id)
    status, result = ts.process(src, job_id, on_progress=cb)

    if status != "done":
        store.update(job_id, status="error", error=result)
        logger.error("Transcription failed: %s", result)
        click.echo(f"\n❌ Error: {result}", err=True)
        sys.exit(1)

    store.update(job_id, status="done", transcript=result, progress=100)
    logger.info("Transcription complete (%d chars)", len(result))

    click.echo(f"\n📝 Transcript ({len(result)} chars):\n")
    click.echo(result)

    if out:
        Path(out).write_text(result)
        logger.info("Transcript written to %s", out)
        click.echo(f"\n💾 Transcript saved to {out}")

    # ── AI processing ──────────────────────────────────────────────────────────
    if ai_mode:
        _run_ai(ai, store, job_id, result, ai_mode, names)


# ── record command ─────────────────────────────────────────────────────────────


@cli.command()
@click.argument("mode", default="default", required=False, metavar="[MODE]")
@click.option(
    "--duration",
    "-d",
    default=None,
    type=int,
    help="Recording duration in seconds (default: record until ENTER)",
)
@click.option("--out", default=None, help="Output WAV path (default: auto-generated)")
@click.option(
    "--names", default="", help="Comma-separated names for spelling correction"
)
@click.option(
    "--no-transcribe",
    "skip_transcribe",
    is_flag=True,
    help="Just record; skip transcription",
)
@click.pass_context
def record(ctx, mode, duration, out, names, skip_transcribe):
    """🎤 Record audio from your microphone and optionally transcribe.

    [MODE]: Optional AI mode to run after transcription (e.g., 'grammar', 'summarize')
    """
    ai_mode = mode
    logger.info("=== record command ===")
    cfg, store, ai, ts = _load_services(ctx.obj["db"])

    # Determine output path
    rec_id = str(uuid.uuid4())[:8]
    wav_path = Path(out) if out else cfg.RECORDINGS_DIR / f"recording_{rec_id}.wav"
    wav_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        record_audio(wav_path, duration=duration)
    except Exception as e:
        logger.error("Recording failed: %s", e)
        click.echo(f"❌ Recording error: {e}", err=True)
        sys.exit(1)

    if skip_transcribe:
        click.echo(
            f"\n⏭  Skipping transcription (--no-transcribe). WAV saved at {wav_path}"
        )
        return

    # Hand off to transcription pipeline
    job = store.create(wav_path.name)
    job_id = job["id"]
    logger.info("Created job %s for recording %s", job_id[:8], wav_path.name)
    store.update(job_id, status="processing")

    click.echo(f"\n🆔 Job ID : {job_id}")
    click.echo("⚙️  Transcribing…\n")

    cb = _progress_callback(store, job_id)

    # We pass the wav directly to ts.process; it expects the source path
    # (it will call convert_to_wav internally, but a WAV → WAV conversion is fine)
    status, result = ts.process(wav_path, job_id, on_progress=cb)

    if status != "done":
        store.update(job_id, status="error", error=result)
        logger.error("Transcription failed: %s", result)
        click.echo(f"\n❌ Error: {result}", err=True)
        sys.exit(1)

    store.update(job_id, status="done", transcript=result, progress=100)
    logger.info("Transcription complete (%d chars)", len(result))

    click.echo(f"\n📝 Transcript:\n")
    click.echo(result)

    if ai_mode:
        _run_ai(ai, store, job_id, result, ai_mode, names)


# ── jobs command ───────────────────────────────────────────────────────────────


@cli.command()
@click.option(
    "--id", "job_id", default=None, help="Show full details for a specific job ID"
)
@click.option(
    "--delete",
    "delete_id",
    default=None,
    metavar="ID",
    help="Delete a specific job by ID",
)
@click.option("--clear", is_flag=True, help="Delete all completed / errored jobs")
@click.option("--limit", default=20, show_default=True, help="Max jobs to list")
@click.pass_context
def jobs(ctx, job_id, delete_id, clear, limit):
    """📋 List, view, and manage transcription jobs."""
    logger.info("=== jobs command ===")
    _, store, _, _ = _load_services(ctx.obj["db"])

    # ── delete a single job ────────────────────────────────────────────────────
    if delete_id:
        if not store.get(delete_id):
            click.echo(f"❌ Job '{delete_id}' not found.", err=True)
            sys.exit(1)
        store.delete(delete_id)
        logger.info("Deleted job %s", delete_id[:8])
        click.echo(f"🗑  Deleted job {delete_id}.")
        return

    # ── clear all completed/errored ────────────────────────────────────────────
    if clear:
        n = store.clear_completed()
        logger.info("Cleared %d jobs", n)
        click.echo(f"🗑  Cleared {n} completed/errored job(s).")
        return

    # ── show single job detail ─────────────────────────────────────────────────
    if job_id:
        job = store.get(job_id)
        if not job:
            click.echo(f"❌ Job '{job_id}' not found.", err=True)
            sys.exit(1)
        _print_job_detail(job)
        return

    # ── list all ───────────────────────────────────────────────────────────────
    all_jobs = store.list_all(limit=limit)
    if not all_jobs:
        click.echo("No jobs found.")
        return

    STATUS_ICON = {
        "queued": "⏳",
        "processing": "⚙️ ",
        "done": "✅",
        "error": "❌",
        "cancelled": "🚫",
    }

    click.echo(f"\n  {'ID':38} {'Status':12} {'AI':4} {'File':28} {'Created'}")
    click.echo("  " + "─" * 97)
    for j in all_jobs:
        ts_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(j["created_at"]))
        icon = STATUS_ICON.get(j["status"], "❓")
        ai_tag = f"[{len(j.get('ai_results') or [])}]" if j.get("ai_results") else "   "
        click.echo(
            f"  {j['id']:38} {icon} {j['status']:10} {ai_tag:4} "
            f"{j['filename'][:28]:28} {ts_str}"
        )
    click.echo(
        f"\n  {len(all_jobs)} job(s) shown. Use --id <uuid> to see transcript & AI results.\n"
    )


# ── modes command ──────────────────────────────────────────────────────────────


@cli.command()
@click.pass_context
def modes(ctx):
    """📋 List available AI processing modes configured in prompts.toml."""
    logger.info("=== modes command ===")
    _, _, ai, _ = _load_services(ctx.obj["db"])

    if not ai.is_configured():
        click.echo("⚠️  AI is not configured (set AI_BASE_URL in your .env).")
        click.echo(ai.get_config_hint())
        return

    info = ai.mode_info()
    if not info:
        click.echo("No AI modes found. Check your prompts.toml.")
        return

    click.echo("\nAvailable AI modes:\n")
    for mode, cfg in info.items():
        name = cfg.get("display_name", mode)
        instruction = cfg.get("instruction", "")
        click.echo(f"  • {mode:20s}  {name}")
        if instruction:
            click.echo(f"    {instruction[:80]}")
        click.echo()


# ── personal names subcommands ───────────────────────────────────────────────


@cli.group()
@click.pass_context
def names(ctx):
    """Manage personal names used by the AI for spelling/formatting."""
    pass


@names.command("list")
@click.pass_context
def names_list(ctx):
    """List personal names currently configured."""
    cfg = __import__("backend.config", fromlist=["Config"]).Config()
    names = cfg.get_personal_names()
    if not names:
        click.echo("No personal names configured.")
        return
    click.echo("Personal names:")
    for n in names:
        click.echo(f" - {n}")


@names.command("add")
@click.argument("names_str")
@click.pass_context
def names_add(ctx, names_str):
    """Add one or more personal names (comma-separated).

    Example: python cli.py names add "Alice,Bob"
    """
    cfg = __import__("backend.config", fromlist=["Config"]).Config()
    existing = cfg.get_personal_names() or []
    new = [n.strip() for n in names_str.split(",") if n.strip()]
    combined = existing[:]
    added = []
    for n in new:
        if n not in combined:
            combined.append(n)
            added.append(n)

    if not added:
        click.echo("No new names to add.")
        return

    ok = cfg.save_personal_names(combined)
    if ok:
        logger.info("Added personal names: %s", ",".join(added))
        click.echo(f"Added: {', '.join(added)}")
    else:
        logger.error("Failed to save personal names")
        click.echo("Error: could not save personal names.", err=True)


# ── ai sub-command (process an existing job) ───────────────────────────────────


@cli.command("ai")
@click.argument("job_id")
@click.argument("mode")
@click.option(
    "--names", default="", help="Comma-separated names for spelling correction"
)
@click.pass_context
@timed
def ai_cmd(ctx, job_id, mode, names):
    """🤖 Rerun AI processing on an existing transcription.

    JOB_ID: ID of the transcription job (see 'jobs' command to list)

    MODE: AI processing mode (e.g., 'grammar', 'summarize')

    Use this command to re-process a transcript or try a different AI mode.
    """
    logger.info("=== ai command (job=%s mode=%s) ===", job_id[:8], mode)
    _, store, ai_svc, _ = _load_services(ctx.obj["db"])

    job = store.get(job_id)
    if not job:
        click.echo(f"❌ Job '{job_id}' not found.", err=True)
        sys.exit(1)

    transcript = job.get("transcript")
    if not transcript:
        click.echo("❌ Job has no transcript yet.", err=True)
        sys.exit(1)

    _run_ai(ai_svc, store, job_id, transcript, mode, names)


# ── helpers ────────────────────────────────────────────────────────────────────


def _run_ai(ai, store, job_id: str, text: str, mode: str, names_str: str):
    """Stream AI processing and store the result."""
    names = (
        [n.strip() for n in names_str.split(",") if n.strip()] if names_str else None
    )

    if not ai.is_configured():
        logger.warning("AI not configured — skipping")
        click.echo("\n⚠️  AI is not configured (set AI_BASE_URL). Skipping AI step.")
        return

    available = ai.available_modes()
    if mode not in available:
        logger.error("Unknown AI mode '%s'. Available: %s", mode, available)
        click.echo(
            f"\n❌ Unknown AI mode '{mode}'. Available: {', '.join(available)}",
            err=True,
        )
        sys.exit(1)

    logger.info(
        "Running AI mode '%s' on job %s (%d chars)", mode, job_id[:8], len(text)
    )
    click.echo(f"\n🤖 AI ({mode}):\n")

    result_parts = []
    try:
        for chunk in ai.process_stream(text, mode, names):
            click.echo(chunk, nl=False)
            result_parts.append(chunk)
    except Exception as e:
        logger.error("AI processing failed: %s", e)
        click.echo(f"\n❌ AI error: {e}", err=True)
        return

    click.echo("\n")
    full_result = "".join(result_parts)
    store.add_ai_result(job_id, mode, full_result)
    logger.info("AI result stored (%d chars)", len(full_result))


def _print_job_detail(job: dict):
    click.echo(f"\n{'─'*60}")
    click.echo(f"ID       : {job['id']}")
    click.echo(f"File     : {job['filename']}")
    click.echo(f"Status   : {job['status']}")
    click.echo(f"Progress : {job['progress']}%")
    click.echo(
        f"Created  : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job['created_at']))}"
    )

    if job.get("error"):
        click.echo(f"Error    : {job['error']}")

    if job.get("transcript"):
        click.echo(f"\n📝 Transcript ({len(job['transcript'])} chars):\n")
        click.echo(job["transcript"])

    if job.get("ai_results"):
        click.echo(f"\n🤖 AI Results ({len(job['ai_results'])}):\n")
        for i, r in enumerate(job["ai_results"]):
            ts_str = time.strftime("%H:%M:%S", time.localtime(r.get("created_at", 0)))
            click.echo(f"  [{i}] mode={r['mode']}  @ {ts_str}")
            click.echo(f"  {r['text']}\n")

    click.echo("─" * 60)


# ── entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
