import tomllib
from pathlib import Path
from typing import OrderedDict
from ollama import chat
import ollama
import os
import logging
import sys

logger = logging.getLogger("log")
logging.basicConfig(level=logging.INFO)


class AIProcessing:
    def __init__(self):
        self.prompts_path = Path(__file__).parent.parent / "prompts.toml"
        self.names_to_fix_file_path = (
            Path(__file__).parent.parent / "personal_names.txt"
        )

        self._modes_with_prompts: OrderedDict = OrderedDict()
        self.load_prompts()

        # Example: AI_MODEL=deepseek-r1:8b-thinking
        self.ollama_model = os.getenv("AI_MODEL", "deepseek-r1:8b-thinking")

        # Enable / disable thinking via env variable (default: True)
        self.enable_thinking = os.getenv("AI_THINK", "true").lower() == "true"

        logger.info(f"Using Ollama model: {self.ollama_model}")
        logger.info(f"Thinking enabled: {self.enable_thinking}")

        self.system_prompt = (
            "You are an expert transcript and journal editor. "
            "Your task is to apply specific editing rules to the provided text "
            "while preserving all original content and meaning.\n\n"
            "Core Instructions:\n"
            "Process Internally: Create a detailed task list of all steps needed "
            "to apply the rules below. Think through each task carefully using internal reasoning.\n"
            "Apply Rules: Execute all tasks from your internal list on the text.\n"
            "Output Strictly: Return ONLY the fully corrected text. "
            "Do NOT show your task list, reasoning, or any explanatory text.\n\n"
            "Editing Rules to Apply:\n"
            "Standard Corrections: Fix grammar, spelling, capitalization, spacing, and punctuation.\n"
            "Duplicate Content: If the same idea is expressed twice, apply the most correct version "
            "and combine them into one clear statement.\n"
            "List Conversion (Conditional): ONLY if the text contains the exact instruction "
            "'make this a list': Convert the relevant content into a markdown list using '-'. "
            "Remove structural cues like 'next' or 'end list'. Do not change the wording of the list items.\n"
            "Heading Conversion (Conditional): ONLY if the text contains the exact instructions "
            "'heading one', 'heading two', or 'heading three': Convert the immediately following "
            "sentence into a markdown heading of the specified level (e.g., #, ##, ###). Add appropriate newlines.\n"
            "Name Spelling: Correct the spelling of personal names using the following reference list "
            "when appropriate.\n\n"
            "Critical Constraints:\n"
            "DO NOT delete, shorten, or summarize any content.\n"
            "DO NOT alter the core meaning or remove any information.\n"
            "Preserve all original sentences and data points.\n"
            "Output must be the corrected text, and nothing else."
        )

    def load_names(self) -> str:
        if not self.names_to_fix_file_path.exists():
            return ""
        with open(self.names_to_fix_file_path, "r", encoding="utf-8") as fp:
            return "".join(fp.readlines())

    def load_prompts(self):
        if self.prompts_path.exists():
            with open(self.prompts_path, "rb") as f:
                config = tomllib.load(f)
            for mode, prompt_config in config.get("prompts", {}).items():
                self._modes_with_prompts[mode] = prompt_config.copy()

    def build_user_prompt(self, mode: str, text: str) -> str:
        if mode not in self._modes_with_prompts:
            raise ValueError(f"Mode '{mode}' not found in prompts.")

        rules = self._modes_with_prompts[mode]["rules"].copy()

        names_list = self.load_names()
        if names_list:
            rules.append(
                "Fix the spelling of personal names using this name list when appropriate:\n"
                f"{names_list}"
            )

        combined_rules = "\n".join(f"- {rule}" for rule in rules)

        return (
            "Apply all of the following rules to the text.\n\n"
            "Rules:\n"
            f"{combined_rules}\n\n"
            "Process:\n"
            "- First, internally list the tasks required to follow all rules, then carry them out.\n"
            "- Do not display your task list or reasoning.\n\n"
            "Output:\n"
            "- Return the full corrected text only.\n"
            "- Do not remove any sentences or information.\n\n"
            f"Text:\n{text}"
        )

    def call_model(self, messages, stream=False):
        options = {}
        if self.enable_thinking:
            options["think"] = True

        try:
            return chat(
                model=self.ollama_model,
                messages=messages,
                options=options,
                stream=stream,
            )
        except ollama.ResponseError as e:
            if e.status_code == 404:
                logger.warning(f"Model not found. Pulling {self.ollama_model}...")
                ollama.pull(self.ollama_model)
                return chat(
                    model=self.ollama_model,
                    messages=messages,
                    options=options,
                    stream=stream,
                )
            else:
                logger.error(f"Ollama error: {e}")
                raise

    def process_prompts(self, mode: str, text: str, stream: bool = False) -> str:
        user_prompt = self.build_user_prompt(mode, text)

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        if stream:
            full_text = []

            for chunk in self.call_model(messages, stream=True):
                delta = chunk.get("message", {}).get("content", "")
                if delta:
                    sys.stdout.write(delta)
                    sys.stdout.flush()
                    full_text.append(delta)

            corrected = "".join(full_text).strip()

        else:
            response = self.call_model(messages, stream=False)
            corrected = response["message"]["content"].strip()

        # Safety check: ensure model didn’t delete large portions
        if len(corrected) < len(text) * 0.6:
            raise RuntimeError("Model response shrank too much — possible deletion.")

        return corrected


if __name__ == "__main__":
    aiproc = AIProcessing()

    test_sentence = (
        "I am a potato that is very sleep. Can fix me? "
        "Alfie, joquin, peter, Rishita. "
        "Heading one. Potato lab. "
        "This is the story of lab fillled with potato. Potatoes."
    )

    # Set stream=True if you want to see tokens (including thinking if model exposes it)
    result = aiproc.process_prompts(
        mode="journal",
        text=test_sentence,
        stream=True,
    )

    print("\n\nFinal result:\n", result)
