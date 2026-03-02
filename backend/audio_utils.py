from pathlib import Path
import os
from datetime import datetime
import subprocess
from typing import Optional


def convert_audio_to_wav(input_file_path: Path) -> Optional[Path]:
    timestamp: int = int(datetime.timestamp(datetime.now()))
    recording_dir: Path = input_file_path.parent / "recordings/"
    os.makedirs(recording_dir, exist_ok=True)
    recording_path = recording_dir / f"{timestamp}.wav"
    # stream = ffmpeg.input(str(input_file_path))
    # # stream = ffmpeg.filter(stream, "ac", ar=16000)
    # stream = ffmpeg.output(stream, str(recording_path), **{"c:a": "pcm_s16le"})
    # ffmpeg.run(stream)
    # ffmpeg -i input.mp3 -ar 16000 -ac 1 -c:a pcm_s16le output.wav
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(input_file_path),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(recording_path),
            ],
        )
        return recording_path
    except Exception as e:
        print(e)
        return None


if __name__ == "__main__":
    test_audio = Path("recordings/20260225_173029_4b415345.webm")
    audio = convert_audio_to_wav(test_audio)
    print(audio)
