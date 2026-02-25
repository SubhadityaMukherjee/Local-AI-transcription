"""AI service for summarization and text processing."""

import json
import tomllib as tomli
import urllib.error as urlerr
import urllib.request as urlreq
from pathlib import Path
from typing import Generator, Optional


class AIService:
    """Service for AI-powered text processing (summarization, grammar correction).

    Prompts are defined in `prompts.toml` under a ``[prompts]`` table.  Each
    section may include any of the following keys:

      * instruction (string) – main task description
      * formatting_rules or rules (list) – extra guidance that will be
        interpolated into the template
      * input_placeholder (string) – template used to insert the text
      * display_name (string, optional) – human‑friendly label for the mode

    New modes can be added at runtime by appending a TOML section; the
    service exposes helpers to enumerate and add modes.
    """

    def __init__(self, config):
        self.config = config
        # ``_prompts`` stores the rendered prompt templates; ``_prompt_configs``
        # preserves the raw configuration from the TOML file for UI/metadata.
        self._prompts = {}
        self._prompt_configs = {}
        self._load_prompts()

    def _load_prompts(self) -> dict:
        """Load AI prompts from TOML config file.

        The method populates both ``self._prompt_configs`` (the raw data from the
        file) and ``self._prompts`` (the rendered prompt template used when
        actually invoking the model).  It returns the latter for backwards
        compatibility with the original implementation.
        """
        self._prompts.clear()
        self._prompt_configs.clear()
        prompts_path = Path(__file__).parent.parent / "prompts.toml"

        if prompts_path.exists():
            with open(prompts_path, "rb") as f:
                config = tomli.load(f)

            for mode, prompt_config in config.get("prompts", {}).items():
                self._prompt_configs[mode] = prompt_config.copy()
                self._prompts[mode] = self._build_prompt(prompt_config)

        return self._prompts

    def is_configured(self) -> bool:
        """Check if AI service is configured."""
        return bool(self.config.AI_BASE_URL)

    def get_prompt(self, mode: str) -> str:
        """Get prompt template for mode, falling back to first available or a bare default."""
        if mode in self._prompts:
            return self._prompts[mode]
        if self._prompts:
            return next(iter(self._prompts.values()))
        return "Process the following text:\n\n{text}"

    def _build_prompt(self, prompt_config: dict) -> str:
        """Return the rendered prompt template for a given mode config.

        This mirrors the logic used when the file is first loaded, so it can be
        reused when new modes are added dynamically.
        """
        instruction = prompt_config.get("instruction", "")
        placeholder = prompt_config.get("input_placeholder", "{text}")

        if "formatting_rules" in prompt_config:
            rules_text = "\n".join(f"- {rule}" for rule in prompt_config["formatting_rules"])
            return f"Task: {instruction}\n\nFormatting rules:\n{rules_text}\n\n{placeholder}"
        elif "rules" in prompt_config:
            rules_text = "\n".join(
                f"{i+1}. {rule}" for i, rule in enumerate(prompt_config["rules"])
            )
            return f"Task: {instruction}\n\nInstructions:\n{rules_text}\n\n{placeholder}"
        else:
            return f"Task: {instruction}\n\n{placeholder}"

    def format_prompt(self, mode: str, text: str, names: list = None) -> str:
        """Format prompt with text and optional personal names."""
        template = self.get_prompt(mode)
        names_str = ", ".join(names) if names else ""
        # Use safe format_map so missing placeholders (e.g. {names} not in template) don't crash
        return template.format_map({"text": text, "names": names_str})

    def process(self, text: str, mode: str = "summarize", names: list = None) -> str:
        """
        Process text with AI.

        Args:
            text: Input text
            mode: Processing mode ('summarize' or 'grammar')
            names: Optional list of personal names for spelling correction

        Returns:
            AI response text
        """
        if not self.is_configured():
            raise ValueError("AI endpoint not configured")

        # Use provided names or get from config
        if names is None:
            names = self.config.get_personal_names()

        prompt = self.format_prompt(mode, text, names)
        payload = json.dumps(
            {
                "model": self.config.AI_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            }
        ).encode()

        url = f"{self.config.AI_BASE_URL.rstrip('/')}/chat/completions"
        req = urlreq.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.AI_API_KEY}",
            },
            method="POST",
        )

        with urlreq.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read())

        return result["choices"][0]["message"]["content"]

    def process_stream(
        self, text: str, mode: str = "summarize", names: list = None
    ) -> Generator[str, None, None]:
        """
        Process text with AI using streaming.

        Args:
            text: Input text
            mode: Processing mode ('summarize' or 'grammar')
            names: Optional list of personal names for spelling correction

        Yields:
            Chunks of AI response text
        """
        if not self.is_configured():
            raise ValueError("AI endpoint not configured")

        # Use provided names or get from config
        if names is None:
            names = self.config.get_personal_names()

        prompt = self.format_prompt(mode, text, names)
        payload = json.dumps(
            {
                "model": self.config.AI_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
            }
        ).encode()

        url = f"{self.config.AI_BASE_URL.rstrip('/')}/chat/completions"
        req = urlreq.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.AI_API_KEY}",
            },
            method="POST",
        )

        with urlreq.urlopen(req, timeout=600) as resp:
            # Read streaming response line by line
            buffer = ""
            while True:
                chunk = resp.read(1024)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8")

                # Process complete lines (SSE format: "data: {...}")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data = line[6:]  # Remove "data: " prefix
                        if data == "[DONE]":
                            return
                        try:
                            parsed = json.loads(data)
                            delta = parsed.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue

    # ------------------------------------------------------------------
    # Mode management helpers
    # ------------------------------------------------------------------

    def available_modes(self) -> list[str]:
        """Return a list of mode keys currently configured."""
        return list(self._prompts.keys())

    def mode_info(self) -> dict:
        """Return metadata for each mode (display name + raw config)."""
        # include display_name fallback to capitalized key
        return {
            mode: {
                "display_name": self._prompt_configs.get(mode, {}).get("display_name")
                or mode.replace("_", " ").capitalize(),
                **self._prompt_configs.get(mode, {}),
            }
            for mode in self.available_modes()
        }

    def add_mode(self, mode: str, prompt_config: dict) -> None:
        """Add a new mode and persist it to prompts.toml.

        ``mode`` must be a valid identifier (alphanumeric and underscores).
        ``prompt_config`` should follow the structure expected in the TOML
        file (instruction, rules/formatting_rules, etc).  If the mode already
        exists a ``ValueError`` is raised.
        """
        if not mode or not mode.replace("_", "").isalnum():
            raise ValueError("Mode name must be alphanumeric/underscores")
        if mode in self._prompts:
            raise ValueError(f"Mode '{mode}' already exists")

        self._prompt_configs[mode] = prompt_config.copy()
        self._prompts[mode] = self._build_prompt(prompt_config)

        # Append to the TOML file.  We don't have a writer library installed,
        # so we build a very simple representation ourselves.  This will not
        # preserve comments or formatting, but is good enough for the purposes
        # of in-app editing.  Using json.dumps gives us valid TOML strings/arrays
        # for our simple values.
        prompts_path = Path(__file__).parent.parent / "prompts.toml"
        lines = ["", f"[prompts.{mode}]"]
        # write display_name first if provided
        if "display_name" in prompt_config:
            lines.append(f"display_name = {json.dumps(prompt_config['display_name'])}")
        if "instruction" in prompt_config:
            lines.append(f"instruction = {json.dumps(prompt_config['instruction'])}")
        if "input_placeholder" in prompt_config:
            lines.append(f"input_placeholder = {json.dumps(prompt_config['input_placeholder'])}")
        if "formatting_rules" in prompt_config:
            lines.append(f"formatting_rules = {json.dumps(prompt_config['formatting_rules'])}")
        if "rules" in prompt_config:
            lines.append(f"rules = {json.dumps(prompt_config['rules'])}")

        with open(prompts_path, "a") as f:
            f.write("\n".join(lines))
            f.write("\n")

    def get_config_hint(self) -> str:
        """Get configuration hint for AI."""
        return (
            "Add to your .env file:\n"
            "  AI_BASE_URL=http://localhost:11434/v1   # Ollama\n"
            "  AI_BASE_URL=http://localhost:1234/v1    # LM Studio\n"
            "Then restart the server."
        )
