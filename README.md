# Local-AI-transcription

I wanted to build a customizable frontend for Whisper.cpp. I know there are a lot of them, but this works for my own quirks

- Note that a bunch of this was vibe coded using Claude/other local models. Please do not try to use this in production. This is at the moment meant to be a local utility only!!
  <img width="1779" height="1052" alt="SCR-20260220-pdzq" src="https://github.com/user-attachments/assets/1a8b98ac-45d0-44b5-aee1-354901fb0907" />
  <img width="956" height="292" alt="SCR-20260220-pjwc" src="https://github.com/user-attachments/assets/d6a9fd9e-3ca0-4f1a-90f3-e59b491b6cb0" />

## Setup

- Make sure you have installed `uv` and `ollama` and `git` and `ffmpeg` on your machine
- Clone this repo
- I have ONLY tested this out on a Mac with an M chip. I was not trying to make it a universal tool yet
- Once you have all this, run `./setup.sh` in this directory
- If all is well, run `./run.sh` and you should be good to go

## Usage

### Recording & Transcription

If you are on linux - install `sudo apt install arecord`. If you are on mac `brew install sox`

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

Once a transcript is ready, the toolbar will show a **mode selector** along with a
single action button. The selector is populated from `prompts.toml` on the
server and you can choose between any of the configured modes (summarize,
grammar, or your own custom modes). Click **+ Mode** to define a new mode using
a simple form; the new prompt will be appended to `prompts.toml` and available
immediately.

The action button label updates to reflect the currently selected mode, and
after processing the right‑hand panel will show results tagged with the mode it
used.

Additional toolbar controls:

- **Copy**: Copy transcript to clipboard
- **Export Markdown**: Download as `.md` file
- **Custom names/words**: There is also an option to add personal names. If you
  find that the AI mispronounces or misunderstands some names or words, you can
  add them in and they will be saved. The LLM will consult this list when
  fixing transcripts.

### AI Results Panel

The right panel shows your AI processing history:

- Click to expand/collapse results
- Use Copy or Export buttons for each result
- Delete unwanted results with the ✕ button

## Customizing Prompts

The `prompts.toml` file controls the set of **modes** that the AI can run. Each
mode corresponds to a top‑level section under `[prompts]` and the UI will
populate a selector based on the file. The list is displayed in the same order
the modes appear in `prompts.toml` (not sorted alphabetically), so you can
easily organize them however you like. You **can** edit `prompts.toml` by hand
and restart the server, or create new modes directly from the web interface.

Below are examples of the built‑in modes:

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
