"""AI service for summarization and text processing."""

import json
import urllib.request as urlreq
import urllib.error as urlerr


class AIService:
    """Service for AI-powered text processing (summarization, grammar correction)."""

    def __init__(self, config):
        self.config = config
        self._prompts = self._load_prompts()

    def _load_prompts(self) -> dict:
        """Load AI prompts."""
        return {
            "summarize": (
                "Task: Summarize the transcript clearly and concisely.\n\n"
                "Formatting rules:\n"
                "- Use Markdown bullet points.\n"
                "- One main idea per bullet.\n"
                "- Do not add information not present in the transcript.\n"
                "- Do not repeat points.\n"
                "- No introduction or conclusion.\n\n"
                "Transcript:\n"
                "{text}"
            ),
            "grammar": (
                "Task: Correct the text.\n\n"
                "Strict rules:\n"
                "1. Fix grammar, spelling, spacing, punctuation, and numbering.\n"
                "2. Do NOT remove content.\n"
                "3. Do NOT add new information.\n"
                "4. Preserve meaning exactly.\n"
                "5. Preserve existing structure unless it is clearly broken.\n"
                "6. If I say make it into a list, format whats below as a proper Markdown list.\n"
                "7. If the word 'next' appears at the beginning of a sentence and indicates the next list item, remove the word and merge it into the list structure.\n"
                "8. If 'next' is part of a normal sentence, keep it.\n"
                "9. Return ONLY the corrected text. No explanations.\n\n"
                "10. Use - for markdown lists\n\n"
                "11. Reflow the text properly\n"
                "Text:\n"
                "{text}"
            ),
        }

    def is_configured(self) -> bool:
        """Check if AI service is configured."""
        return bool(self.config.AI_BASE_URL)

    def get_prompt(self, mode: str) -> str:
        """Get prompt template for mode."""
        return self._prompts.get(mode, self._prompts["summarize"])

    def format_prompt(self, mode: str, text: str) -> str:
        """Format prompt with text."""
        template = self.get_prompt(mode)
        return template.format(text=text)

    def process(self, text: str, mode: str = "summarize") -> str:
        """
        Process text with AI.

        Args:
            text: Input text
            mode: Processing mode ('summarize' or 'grammar')

        Returns:
            AI response text
        """
        if not self.is_configured():
            raise ValueError("AI endpoint not configured")

        prompt = self.format_prompt(mode, text)
        payload = json.dumps({
            "model": self.config.AI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }).encode()

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

        with urlreq.urlopen(req, timeout=120) as resp:
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
