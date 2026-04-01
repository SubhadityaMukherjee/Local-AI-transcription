# Local-AI-transcription

I wanted to build a customizable CLI for Whisper.cpp that uses a Local LLM to do post-processing on the transcripts with modes/personal names etc. So here it is.

## Setup

- Make sure you have installed `uv` and `opencode` and `git` and `ffmpeg` on your machine
- Install opencode with `npm install -g opencode`
- If you are on linux - install `sudo apt install arecord`. If you are on mac `brew install sox`
- Clone this repo
- I have ONLY tested this out on a Mac with an M chip. I was not trying to make it a universal tool yet
- Once you have all this, run `./setup.sh` in this directory
- If all is well, run `./run.sh` and you should be good to go

### AI Service Configuration

By default, this project uses **opencode** for AI processing. You can configure the AI service by setting environment variables:

- `AI_SERVICE`: Choose between `opencode` (default) or `ollama`
- `AI_MODEL`: The model to use (default: `zai-coding-plan/glm-4.7` for opencode, or `deepseek-r1:latest` for ollama)

**For opencode (default):**
```bash
AI_SERVICE=opencode
AI_MODEL=zai-coding-plan/glm-4.7
```

**For ollama (optional):**
```bash
AI_SERVICE=ollama
AI_BASE_URL=http://localhost:11434/v1
AI_MODEL=deepseek-r1:latest
```

Note: If using ollama, install the optional dependency with `pip install ollama` or `uv sync --extra ollama`

## Usage

### CLI Usage

The project includes a CLI for scripting and batch operations. Use `python cli.py --help` for full documentation.

#### Basic Commands

**Record audio from your microphone:**

```bash
python cli.py record
python cli.py record grammar              # Record and run AI grammar check
python cli.py record -d 10                # Record for 10 seconds
python cli.py record --out /path/to/file.wav
```

**Transcribe an existing audio/video file:**

```bash
python cli.py transcribe /path/to/file.mp3
python cli.py transcribe /path/to/file.mp3 grammar      # Transcribe and run AI mode
python cli.py transcribe /path/to/file.mp3 --out result.txt
python cli.py transcribe /path/to/file.mp3 --names "john,mary"
```

**Manage transcription jobs:**

```bash
python cli.py jobs                        # List all jobs
python cli.py jobs --limit 50             # List last 50 jobs
python cli.py jobs --id <job-uuid>        # Show full details of a job
python cli.py jobs --delete <job-uuid>    # Delete a specific job
python cli.py jobs --clear                # Delete all completed/errored jobs
```

**View and run AI modes:**

```bash
python cli.py modes                       # List all available AI modes
python cli.py ai <job-uuid> grammar       # Run AI grammar check on existing job
python cli.py ai <job-uuid> summarize     # Run AI summarization on existing job
python cli.py ai <job-uuid> grammar --names "john,mary"
```

**Personal names:**

```bash
python cli.py names list                  # Show configured personal names
python cli.py names add "Alice,Bob"     # Add new personal names (comma-separated)
```

#### Options

- `--verbose` or `-v`: Enable debug logging (shows SQL queries, detailed progress)
- `--db`: Use SQLAlchemy job store instead of JSON (requires DATABASE_URL env var)

#### Examples

**Record and auto-fix transcript in one command:**

```bash
python cli.py record grammar
```

**Transcribe batch of files:**

```bash
for file in recordings/*.mp3; do
  echo "Processing $file..."
  python cli.py transcribe "$file" grammar
done
```

**Transcribe in verbose mode (see detailed logs):**

```bash
python cli.py -v transcribe /path/to/file.mp3
```

## Features

- Records from microphone (fixed duration or manual stop)
- Transcribes audio/video files (mp3, wav, flac, m4a, mp4, mkv, mov)
- Stores jobs with progress tracking and history
- AI post-processing with configurable modes (grammar fix, summarization, custom)
- Supports custom names/words for better recognition
- Progress bars and real-time updates
- SQL or JSON job storage

## Customizing Prompts

The `prompts.toml` file controls the set of **AI modes**. Each mode corresponds to a top-level section under `[prompts]`. The CLI will use these modes.

### Example Modes

**Summarize Prompt:**

```toml
[prompts.summarize]
instruction = "Your instructions here."
formatting_rules = [
    "Rule 1",
    "Rule 2",
]
input_placeholder = "Transcript: {text}"
```

**Grammar/Auto-fix Prompt:**

```toml
[prompts.grammar]
instruction = "Your instructions here."
rules = [
    "Rule 1",
    "Rule 2",
]
input_placeholder = "Text: {text}"
```

### Configuration Options

- **instruction**: Main task description for the AI
- **formatting_rules** (for summarize): List of bullet points for formatting
- **rules** (for grammar): Numbered list of processing rules
- **input_placeholder**: Template for input text (`{text}` is replaced with transcript)

### Available Structural Commands

- `begin list` / `end list` - Bullet lists
- `begin sublist` / `end sublist` - Indented sub-items
- `begin numbered list` / `end numbered list` - Ordered lists
- `new paragraph` - Paragraph break
- `line break` - Single line break
- `heading level one/two/three` - Markdown headings
- `begin quote` / `end quote` - Blockquotes
- `begin code` / `end code` - Fenced code blocks

Edit `prompts.toml` directly - changes apply on next run.
