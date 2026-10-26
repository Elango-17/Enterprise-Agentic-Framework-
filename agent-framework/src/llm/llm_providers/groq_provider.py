"""Groq provider functionality for the Enterprise Agent Framework."""

from __future__ import annotations

import re
from typing import Any

from groq import Groq

from src.exceptions import (
    handle_llm_call,
    parse_llm_json,
    validate_llm_content,
)
from src.llm.base import LLMProvider


class GroqProvider(LLMProvider):
    """Groq LLM provider."""

    def __init__(
        self,
        api_key: str,
        model: str,
    ) -> None:
        """Initialize the Groq provider."""

        self.client = Groq(
            api_key=api_key
        )
        self.model = model

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """Generate a JSON response using Groq."""

        response = handle_llm_call(
            "Groq request",
            self.client.chat.completions.create,
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            temperature=0,
            max_tokens=4096,
            response_format={
                "type": "json_object",
            },
        )

        content = validate_llm_content(
            response.choices[0].message.content,
            "Groq",
        )

        clean_text = re.sub(
            r"^```(?:json)?\s*",
            "",
            content,
            flags=re.IGNORECASE,
        )

        clean_text = re.sub(
            r"\s*```$",
            "",
            clean_text,
        ).strip()

        return parse_llm_json(
            clean_text,
            "Groq",
        )

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Generate a text response using Groq."""

        response = handle_llm_call(
            "Groq request",
            self.client.chat.completions.create,
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            temperature=0,
            max_tokens=4096,
        )

        return validate_llm_content(
            response.choices[0].message.content,
            "Groq",
        )