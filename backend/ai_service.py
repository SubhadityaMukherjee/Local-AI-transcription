"""AI service for summarization and text processing."""

import json
import tomllib as tomli
import urllib.error as urlerr
import urllib.request as urlreq
from pathlib import Path


class AIService:
    """Service for AI-powered text processing (summarization, grammar correction)."""

    def __init__(self, config):
        self.config = config
        self._prompts = self._load_prompts()

    def _load_prompts(self) -> dict:
        """Load AI prompts from TOML config file."""
        prompts = {}
        prompts_path = Path(__file__).parent.parent / "prompts.toml"

        if prompts_path.exists():
            with open(prompts_path, "rb") as f:
                config = tomli.load(f)

            for mode, prompt_config in config.get("prompts", {}).items():
                instruction = prompt_config.get("instruction", "")
                placeholder = prompt_config.get("input_placeholder", "{text}")

                # Build prompt from instruction, rules/formatting_rules, and placeholder
                if "formatting_rules" in prompt_config:
                    rules_text = "\n".join(
                        f"- {rule}" for rule in prompt_config["formatting_rules"]
                    )
                    prompt = f"Task: {instruction}\n\nFormatting rules:\n{rules_text}\n\n{placeholder}"
                elif "rules" in prompt_config:
                    rules_text = "\n".join(
                        f"{i+1}. {rule}"
                        for i, rule in enumerate(prompt_config["rules"])
                    )
                    prompt = f"Task: {instruction}\n\nInstructions:\n{rules_text}\n\n{placeholder}"
                else:
                    prompt = f"Task: {instruction}\n\n{placeholder}"

                prompts[mode] = prompt

        # Fallback to empty dict if file doesn't exist or is empty
        return prompts

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

    def get_config_hint(self) -> str:
        """Get configuration hint for AI."""
        return (
            "Add to your .env file:\n"
            "  AI_BASE_URL=http://localhost:11434/v1   # Ollama\n"
            "  AI_BASE_URL=http://localhost:1234/v1    # LM Studio\n"
            "Then restart the server."
        )
