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
                "Task: Clean and lightly structure the text.\n\n"
                "Instructions:\n"
                "1. Fix grammar, spelling, spacing, and punctuation only.\n"
                "2. Preserve the original meaning and tone.\n"
                "3. Do NOT summarize.\n"
                "4. Do NOT rephrase for style unless required for grammar clarity.\n"
                "5. Do NOT reorganize sentences.\n"
                "6. Only create a Markdown list if the speaker explicitly signals a list using words such as:\n"
                "   'list', 'first', 'second', 'third', 'next item', 'another item', etc.\n"
                "7. Do NOT infer lists from narration, sequential sentences, or related ideas.\n"
                "8. If no explicit list signal appears, keep the text as normal paragraphs.\n"
                "9. If the speaker explicitly says 'sublist', nest items under the most recent main list item.\n"
                "10. Remove phrases like 'end of list' or 'end of sublist'.\n"
                "11. Use '-' for bullet points.\n"
                "12. Use two spaces for indentation for nested items.\n"
                "13. Return only the final Markdown.\n\n"
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
