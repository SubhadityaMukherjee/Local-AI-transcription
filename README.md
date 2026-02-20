# Local-AI-transcription

I wanted to build a customizable frontend for Whisper.cpp. I know there are a lot of them, but this works for my own quirks

- Note that a bunch of this was vibe coded using Claude/other local models. Please do not try to use this in production. This is at the moment meant to be a local utility only!!

## Setup

- Make sure you have installed `uv` and `ollama` and `git` and `ffmpeg` on your machine
- Clone this repo
- I have ONLY tested this out on a Mac with an M chip. I was not trying to make it a universal tool yet
- Once you have all this, run `./setup.sh` in this directory
- If all is well, run `./run.sh` and you should be good to go

## Usage

### Recording & Transcription

1. **Record Audio**: Click the red record button to start recording from your microphone. Click again to stop.
2. **Append Recording**: Click the green `+` button to append new recording to an existing job.
3. **Upload File**: Drag and drop or click to upload audio/video files (mp3, wav, flac, m4a, mp4, mkv, mov).

### Auto-Fix Toggle

In the header, there's a toggle switch labeled **"Auto-fix on complete"**. When enabled:
- The AI will automatically run the "Auto fix text" (grammar/structure fix) action when transcription completes
- This saves you a click after each recording
- The setting persists in your browser's localStorage

### Progress Tracking

- The **header progress bar** shows transcription progress (Queued → Converting → Transcribing)
- When auto-fix runs, the progress bar turns green to show AI processing status
- This is visible even when the jobs panel is collapsed

### AI Actions

Once a transcript is ready, use the toolbar buttons:
- **Summarize**: Generate a bullet-point summary of the transcript
- **Auto fix text**: Clean up grammar, spelling, punctuation, and apply structural commands
- **Copy**: Copy transcript to clipboard
- **Export Markdown**: Download as `.md` file

### AI Results Panel

The right panel shows your AI processing history:
- Click to expand/collapse results
- Use Copy or Export buttons for each result
- Delete unwanted results with the ✕ button

## Customizing Prompts

The `prompts.toml` file controls how the AI processes your transcripts. You can customize:

### Summarize Prompt

```toml
[prompts.summarize]
instruction = "Your instructions here."
formatting_rules = [
    "Rule 1",
    "Rule 2",
]
input_placeholder = "Transcript: {text}"
```

### Grammar/Auto-fix Prompt

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

### Example: Custom Grammar Rules

To add custom structural commands, add them to the grammar rules:

```toml
[rules]
# Your custom rules here
```

Available structural commands in default config:
- `begin list` / `end list` - Bullet lists
- `begin sublist` / `end sublist` - Indented sub-items  
- `begin numbered list` / `end numbered list` - Ordered lists
- `new paragraph` - Paragraph break
- `line break` - Single line break
- `heading level one/two/three` - Markdown headings
- `begin quote` / `end quote` - Blockquotes
- `begin code` / `end code` - Fenced code blocks

After editing `prompts.toml`, restart the server to apply changes.
