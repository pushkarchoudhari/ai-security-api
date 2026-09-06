"""Intent classifier with graceful degradation.

Design points that matter for security review:

  * The Gemini client is constructed lazily. Importing this module never touches
    the network or requires a key, which is what makes the app testable and what
    stops a missing GEMINI_API_KEY from crashing the process at startup.
  * Live calls are capped by a hard budget. Exhausting it degrades to the
    deterministic classifier rather than failing requests or draining a quota --
    a publicly reachable instance cannot become someone else's free inference.
  * Model output is scanned by the output guardrails before it is parsed or
    trusted, and the action is clamped to a known set in the Intent model.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from app.config import Settings
from app.llm import fallback
from app.llm.prompts import build_system_prompt
from app.models import Intent
from app.security.guardrails import OutputVerdict, scan_model_output


@dataclass
class ClassificationResult:
    intent: Intent
    verdict: OutputVerdict | None = None
    degraded: bool = False
    reason: str | None = None


class IntentClassifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._system_prompt = build_system_prompt(settings.canary_token)
        self._client = None
        self._lock = threading.Lock()
        self._calls_made = 0

    # -- budget ------------------------------------------------------------
    @property
    def calls_made(self) -> int:
        return self._calls_made

    def _reserve_call(self) -> bool:
        with self._lock:
            if self._calls_made >= self._settings.llm_daily_call_budget:
                return False
            self._calls_made += 1
            return True

    # -- lazy client -------------------------------------------------------
    def _get_client(self):
        if self._client is None:
            from google import genai  # imported here to keep startup cheap

            self._client = genai.Client(api_key=self._settings.gemini_api_key)
        return self._client

    # -- classification ----------------------------------------------------
    def classify(self, question: str) -> ClassificationResult:
        if not self._settings.llm_live:
            return ClassificationResult(
                intent=fallback.classify(question),
                degraded=True,
                reason="mock_mode",
            )

        if not self._reserve_call():
            return ClassificationResult(
                intent=fallback.classify(question),
                degraded=True,
                reason="llm_budget_exhausted",
            )

        try:
            raw = self._invoke(question)
        except Exception as exc:  # noqa: BLE001 - any upstream failure degrades
            return ClassificationResult(
                intent=fallback.classify(question),
                degraded=True,
                reason=f"{type(exc).__name__}: {exc}"[:200],
            )

        verdict = scan_model_output(raw, self._settings.canary_token)
        if not verdict.safe:
            # Never parse or return output that tripped a guardrail.
            return ClassificationResult(
                intent=Intent(action="general_answer", params={}, source="guardrail_blocked"),
                verdict=verdict,
                degraded=True,
                reason="output_guardrail",
            )

        try:
            intent = self._parse(raw)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            return ClassificationResult(
                intent=fallback.classify(question),
                verdict=verdict,
                degraded=True,
                reason=f"unparseable_response: {exc}"[:200],
            )

        return ClassificationResult(intent=intent, verdict=verdict)

    def _invoke(self, question: str) -> str:
        from google import genai

        response = self._get_client().models.generate_content(
            model=self._settings.gemini_model,
            contents=f"User question: {question}",
            config=genai.types.GenerateContentConfig(
                system_instruction=self._system_prompt,
                temperature=0.0,
                max_output_tokens=200,
            ),
        )
        text = getattr(response, "text", None)
        if not text:
            raise ValueError("empty response from model")
        return str(text).strip()

    @staticmethod
    def _parse(raw: str) -> Intent:
        if raw.startswith("```"):
            body = raw.split("\n", 1)[1] if "\n" in raw else ""
            raw = body.rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        if not isinstance(data, dict) or "action" not in data:
            raise ValueError("response missing 'action'")
        params = data.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("'params' must be an object")
        # Intent's validator clamps any unknown action to general_answer.
        return Intent(action=str(data["action"]), params=params, source="llm")
