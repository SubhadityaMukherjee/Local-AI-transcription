import os
import sys
from pathlib import Path
import tempfile
import uuid

# make workspace root importable
sys.path.insert(0, os.getcwd())

import pytest
from click.testing import CliRunner

import cli


def test_concat_audio_order(monkeypatch, tmp_path):
    # create two dummy files with names that will be sorted
    f1 = tmp_path / "b.wav"
    f2 = tmp_path / "a.wav"
    f1.write_text("")
    f2.write_text("")

    # capture the command executed by ffmpeg
    ff_calls = []

    def fake_run(cmd, check):
        ff_calls.append(cmd)
        # verify the list file contents inside the command
        list_idx = cmd.index("-i") + 1
        list_path = cmd[list_idx]
        with open(list_path) as f:
            lines = [l.strip() for l in f.readlines()]
        # order should be alphabetical by filename (a.wav first)
        assert lines == [f"file '{f2.resolve()}'", f"file '{f1.resolve()}'"]
        # create an empty destination to simulate success
        Path(cmd[-1]).write_text("")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.os, "unlink", lambda p: None)

    out_path = tmp_path / "out.wav"
    cli._concat_audio_files([f1, f2], out_path)
    assert ff_calls
    assert out_path.exists()


def make_dummy_services(tmp_path):
    class DummyCfg:
        UPLOAD_DIR = tmp_path

    class DummyStore:
        def create(self, name):
            return {"id": uuid.uuid4().hex}

    class DummyTS:
        def process(self, src, job_id, on_progress=None):
            # record the source that was transcribed
            self.last_src = src
            return "done", "dummy text"

    return DummyCfg(), DummyStore(), None, DummyTS()


def test_transcribe_multiple_files(monkeypatch, tmp_path):
    runner = CliRunner()
    f1 = tmp_path / "z.wav"
    f2 = tmp_path / "y.wav"
    f1.write_text("")
    f2.write_text("")

    # fake concat helper so we can inspect inputs/outputs without running ffmpeg
    called = {}

    def fake_concat(sources, dst):
        called['sources'] = sources.copy()
        dst.write_text("")

    monkeypatch.setattr(cli, "_concat_audio_files", fake_concat)
    # patch service loader
    def fake_load(db):
        return make_dummy_services(tmp_path)

    monkeypatch.setattr(cli, "_load_services", fake_load)

    result = runner.invoke(cli.cli, ["transcribe", str(f1), str(f2)])
    assert result.exit_code == 0, result.output

    # verify that concat was called with sorted list
    assert called['sources'] == [f2, f1]


def test_service_chunking(monkeypatch, tmp_path):
    # simulate a long WAV and verify that transcribe splits into chunks
    from backend import transcription_service

    class Cfg:
        UPLOAD_DIR = tmp_path
        OUTPUT_DIR = tmp_path
        WHISPER_BIN = "/bin/true"
        WHISPER_MODEL = "dummy"
        CHUNK_THRESHOLD = 1  # very small so we always split

    svc = transcription_service.TranscriptionService(Cfg())

    # fake helpers
    monkeypatch.setattr(svc, "_get_duration", lambda wav: 10)
    monkeypatch.setattr(svc, "_split_wav", lambda wav, max_secs=300: [tmp_path / "a.wav", tmp_path / "b.wav"])

    recorded = []
    progress_calls = []

    def fake_run(wav, out_stem, prog, cancel):
        recorded.append((wav.name, out_stem))
        # simulate two progress updates per chunk
        prog({"stage": "transcribing", "pct": 0})
        prog({"stage": "transcribing", "pct": 100})
        return f"hello from {wav.name}"

    monkeypatch.setattr(svc, "_run_whisper", fake_run)

    def cb(p):
        progress_calls.append(p.copy())

    text = svc.transcribe(tmp_path / "dummy.wav", "job123", progress_callback=cb)
    assert "hello from a.wav" in text
    assert "hello from b.wav" in text
    assert len(recorded) == 2

    # two chunks each produced two callbacks; percentages should increase
    assert len(progress_calls) == 4
    # compute expected percentages: with n=2, span=32 (65//2); base for chunk0=30
    # chunk1 base=30+32=62; expect sequence 30, 62
    assert progress_calls[0]["pct"] >= 30
    assert progress_calls[1]["pct"] <= progress_calls[2]["pct"]
