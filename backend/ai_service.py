"""AI service for summarization and text processing (streaming-first)."""

import json
import tomllib as tomli
import urllib.request as urlreq
from pathlib import Path
from typing import Generator
import codecs
import ollama


class AIService:
    """Service for AI-powered text processing (streaming-first)."""

    def __init__(self, config):
        self.config = config
        self._prompts: dict[str, str] = {}
        self._prompt_configs: dict[str, dict] = {}
        self._load_prompts()

    # ------------------------------------------------------------------
    # Prompt loading
    # ------------------------------------------------------------------

    def _load_prompts(self) -> None:
        self._prompts.clear()
        self._prompt_configs.clear()

        prompts_path = Path(__file__).parent.parent / "prompts.toml"
        if not prompts_path.exists():
            return

        with open(prompts_path, "rb") as f:
            config = tomli.load(f)

        for mode, prompt_config in config.get("prompts", {}).items():
            self._prompt_configs[mode] = prompt_config.copy()
            self._prompts[mode] = self._build_prompt(prompt_config)

    def _build_prompt(self, prompt_config: dict) -> str:
        instruction = prompt_config.get("instruction", "")
        placeholder = prompt_config.get("input_placeholder", "{text}")

        if "formatting_rules" in prompt_config:
            rules = "\n".join(f"- {r}" for r in prompt_config["formatting_rules"])
            return f"Task: {instruction}\n\nFormatting rules:\n{rules}\n\n{placeholder}"

        if "rules" in prompt_config:
            rules = "\n".join(
                f"{i+1}. {r}" for i, r in enumerate(prompt_config["rules"])
            )
            return f"Task: {instruction}\n\nInstructions:\n{rules}\n\n{placeholder}"

        return f"Task: {instruction}\n\n{placeholder}"

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self.config.AI_BASE_URL)

    def available_modes(self) -> list[str]:
        return list(self._prompts.keys())

    def get_prompt(self, mode: str) -> str:
        return self._prompts.get(mode) or next(
            iter(self._prompts.values()),
            "Process the following text:\n\n{text}",
        )

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def format_prompt(self, mode: str, text: str, names=None) -> str:
        template = self.get_prompt(mode)
        names_str = ", ".join(names or [])
        return template.format_map({"text": text, "names": names_str})

    def build_system_prompt(self, prompt_config: dict, names=None) -> str:
        instruction = prompt_config.get("instruction", "")
        rules = prompt_config.get("rules", [])
        names_str = ", ".join(names or [])

        rules_text = "\n".join(
            f"{i+1}. {r.format(names=names_str)}" for i, r in enumerate(rules)
        )

        return f"{instruction}\n\nRules:\n{rules_text}" if rules else instruction

    def _build_messages(self, text: str, mode: str, names) -> list[dict]:
        if mode not in self._prompt_configs:
            raise ValueError(f"Unknown mode: {mode}")

        prompt = self.format_prompt(mode, text, names)
        system = self.build_system_prompt(self._prompt_configs[mode], names)

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

    # ------------------------------------------------------------------
    # Streaming-first execution
    # ------------------------------------------------------------------

    def process(self, text: str, mode: str = "summarize", names=None) -> str:
        """Non-streaming API built on top of streaming."""
        return "".join(self.process_stream(text, mode, names))

    def process_stream(
        self, text: str, mode: str = "summarize", names=None
    ) -> Generator[str, None, None]:

        if not self.is_configured():
            raise ValueError("AI endpoint not configured")

        names = names if names is not None else self.config.get_personal_names()
        messages = self._build_messages(text, mode, names)

        # Convert to Ollama message format (same structure)
        response = ollama.chat(
            model=self.config.AI_MODEL,
            messages=messages,
            stream=True,
        )

        for chunk in response:
            content = chunk.get("message", {}).get("content")
            if content:
                yield content

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _make_request(self, payload: bytes) -> urlreq.Request:
        url = f"{self.config.AI_BASE_URL.rstrip('/')}/chat/completions"
        return urlreq.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.AI_API_KEY}",
            },
            method="POST",
        )

    def _stream_response(self, resp) -> Generator[str, None, None]:
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""

        for chunk in iter(lambda: resp.read(4096), b""):
            # Incremental decode handles split multibyte characters safely
            text = decoder.decode(chunk)
            buffer += text

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()

                if not line or not line.startswith("data:"):
                    continue

                data = line.removeprefix("data:").strip()

                if data == "[DONE]":
                    return

                try:
                    parsed = json.loads(data)
                    delta = parsed["choices"][0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

        # Flush remaining decoder buffer (rare but correct)
        remainder = decoder.decode(b"", final=True)
        if remainder:
            buffer += remainder

    # ------------------------------------------------------------------
    # Mode management
    # ------------------------------------------------------------------

    def mode_info(self) -> dict:
        return {
            mode: {
                "display_name": cfg.get("display_name")
                or mode.replace("_", " ").capitalize(),
                **cfg,
            }
            for mode, cfg in self._prompt_configs.items()
        }

    def add_mode(self, mode: str, prompt_config: dict) -> None:
        if not mode or not mode.replace("_", "").isalnum():
            raise ValueError("Mode name must be alphanumeric/underscores")

        if mode in self._prompts:
            raise ValueError(f"Mode '{mode}' already exists")

        self._prompt_configs[mode] = prompt_config.copy()
        self._prompts[mode] = self._build_prompt(prompt_config)

        prompts_path = Path(__file__).parent.parent / "prompts.toml"
        lines = ["", f"[prompts.{mode}]"]

        for key in (
            "display_name",
            "instruction",
            "input_placeholder",
            "formatting_rules",
            "rules",
        ):
            if key in prompt_config:
                lines.append(f"{key} = {json.dumps(prompt_config[key])}")

        with open(prompts_path, "a") as f:
            f.write("\n".join(lines) + "\n")

    # ------------------------------------------------------------------

    def get_config_hint(self) -> str:
        return (
            "Add to your .env file:\n"
            "  AI_BASE_URL=http://localhost:11434/v1   # Ollama\n"
            "  AI_BASE_URL=http://localhost:1234/v1    # LM Studio\n"
            "Then restart the server."
        )
