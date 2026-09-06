"""Request and response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.config import get_settings

_settings = get_settings()

VALID_ACTIONS = ("create_ticket", "generate_report", "lookup_employee", "general_answer")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=_settings.max_question_length)
    api_key: str = Field(..., min_length=10)

    @field_validator("question")
    @classmethod
    def strip_question(cls, v: str) -> str:
        stripped = v.strip()
        if len(stripped) < 3:
            raise ValueError("question must contain at least 3 non-whitespace characters")
        return stripped

    @field_validator("api_key")
    @classmethod
    def validate_key_format(cls, v: str) -> str:
        if not v.startswith("sk-"):
            raise ValueError("API key must start with 'sk-'")
        return v


class Intent(BaseModel):
    """Normalised output of the intent classifier.

    The action is constrained to a known set here, at the boundary, so that no
    model output can ever reach the dispatcher as an unrecognised action.
    """

    action: str = "general_answer"
    params: dict[str, Any] = Field(default_factory=dict)
    source: str = "llm"

    @field_validator("action")
    @classmethod
    def clamp_action(cls, v: str) -> str:
        return v if v in VALID_ACTIONS else "general_answer"
