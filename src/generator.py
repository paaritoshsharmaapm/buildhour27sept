"""The Groq call, in JSON mode (architecture.md §8 Q8, §14 E2-E4).

Two invariants shape this module:

I1/ADR-02 — the LLM never returns a URL. It returns a source_id, and the
    registry resolves that to the link. A fabricated URL cannot reach a user
    because the model never types one.

TB-3 — text inside <context> is reference data, never instructions. Corpus pages
    are our own vetted URLs, so exposure is low, but a scraped "ignore previous
    instructions" line must not become a system turn.

On any failure this returns an `LLMResult` carrying an `error` and an empty
answer. It never falls back to echoing raw model text: that is precisely how
unparseable output turns into a plausible-looking wrong answer in the UI.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from functools import lru_cache

from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError, OpenAI

from src.config import CONFIG

TRANSIENT_BACKOFF_S = 2.0
RATE_LIMIT_BACKOFF_S = 8.0
from src.prompts import SYSTEM_PROMPT, render_context, render_question


class LLMNotConfigured(RuntimeError):
    """No API key. Raised instead of KeyError so the UI can say what to do."""


@dataclass(frozen=True)
class LLMResult:
    answer: str
    source_id: str
    off_topic: bool
    attempts: int
    latency_ms: int
    raw_response: str = ""
    error: str | None = None


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    """Cached so the HTTP connection is reused across turns.

    A missing key raises `LLMNotConfigured` with an actionable message rather
    than a bare KeyError from deep inside the SDK.
    """
    key = os.environ.get(CONFIG.GROQ_API_KEY_ENV)
    if not key:
        raise LLMNotConfigured(
            f"{CONFIG.GROQ_API_KEY_ENV} is not set. Copy .env.example to .env, "
            f"add your Groq key, or export {CONFIG.GROQ_API_KEY_ENV}=gsk_..."
        )
    return OpenAI(base_url=CONFIG.GROQ_BASE_URL, api_key=key)


def _failure(error: str, attempts: int, latency_ms: int, raw: str = "") -> LLMResult:
    return LLMResult(
        answer="",
        source_id="",
        off_topic=True,
        attempts=attempts,
        latency_ms=latency_ms,
        raw_response=raw,
        error=error,
    )


def generate(query: str, hits: list, registry=None) -> LLMResult:
    """One Groq round-trip. Exactly one retry (NFR-2), never a retry loop."""
    started = time.time()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": render_context(hits) + "\n" + render_question(query)},
    ]

    def elapsed() -> int:
        return int((time.time() - started) * 1000)

    last_error = "api_error"
    # Exactly one retry, per NFR-2. The original 2s flat backoff was shorter
    # than Groq's rate-limit window, so a throttled run burned both attempts in
    # ~4s and reported ERROR. Rate limits get a longer, single wait; transient
    # connection errors keep the short one so latency does not regress.
    backoff = TRANSIENT_BACKOFF_S
    for attempt in range(1, 3):
        try:
            response = get_client().chat.completions.create(
                model=CONFIG.GROQ_MODEL,
                messages=messages,
                temperature=CONFIG.LLM_TEMPERATURE,
                max_tokens=CONFIG.LLM_MAX_TOKENS,
                response_format={"type": "json_object"},
                timeout=CONFIG.LLM_TIMEOUT_S,
            )
        except (APITimeoutError, APIConnectionError):
            last_error = "unreachable"
        except RateLimitError:
            last_error = "rate_limited"
            backoff = RATE_LIMIT_BACKOFF_S
        except APIStatusError as error:
            last_error = "api_error"
            if getattr(error, "status_code", None) in (400, 401, 403):
                # A malformed request or a bad key will not fix itself. Only
                # transient conditions are worth the second attempt.
                return _failure(last_error, attempt, elapsed())
        except Exception:
            last_error = "api_error"
        else:
            break
        if attempt == 1:
            time.sleep(backoff)  # single backoff, then one more try
    else:
        return _failure(last_error, 2, elapsed())

    raw = response.choices[0].message.content or ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # No fallback to `raw`: echoing unparsed model text is the failure mode
        # this architecture exists to prevent.
        return _failure("invalid_json", 1, elapsed(), raw)

    answer = payload.get("answer")
    source_id = payload.get("source_id")
    off_topic = payload.get("off_topic")
    if not isinstance(off_topic, bool):
        return _failure("schema", 1, elapsed(), raw)

    if off_topic:
        # An honest decline is a SUCCESSFUL generation, not a schema error.
        # This used to score `off_topic: true` with an empty answer as invalid,
        # so pipeline returned ERROR / "I couldn't reach the language model"
        # and format_answer's NO_GROUNDING branch was unreachable: a question
        # genuinely absent from the corpus was reported to the user as a
        # network failure. Any answer text is discarded here — a model that
        # sets off_topic while still writing prose is not to be trusted.
        return LLMResult(
            answer="",
            source_id="",
            off_topic=True,
            attempts=1,
            latency_ms=elapsed(),
            raw_response=raw,
        )

    if not isinstance(answer, str) or not answer.strip():
        return _failure("schema", 1, elapsed(), raw)
    if not isinstance(source_id, str) or not source_id.strip():
        return _failure("schema", 1, elapsed(), raw)

    return LLMResult(
        answer=answer,
        source_id=source_id.strip(),
        off_topic=off_topic,
        attempts=1,
        latency_ms=elapsed(),
        raw_response=raw,
    )
