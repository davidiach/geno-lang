"""
LLM Client — Multi-Provider Support
====================================

Clients for Anthropic Claude, OpenAI GPT, and Google Gemini,
with prompt caching, retry logic, and a factory function that
auto-detects the right provider from the model name.

Usage:
    client = create_client("claude-sonnet-4-6")
    runner.set_generator(client.generate)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar, cast

from experiment.prompts import GENOTYPE_SPEC

logger = logging.getLogger(__name__)
T = TypeVar("T")

# ---------------------------------------------------------------------------
# Lazy imports — each SDK is optional
# ---------------------------------------------------------------------------

try:
    import anthropic  # type: ignore[import-not-found]
except ImportError:
    anthropic = None  # type: ignore[assignment]

try:
    import openai  # type: ignore[import-not-found]
except ImportError:
    openai = None  # type: ignore[assignment]

try:
    import google.generativeai as genai  # type: ignore[import-not-found, import-untyped]
except ImportError:
    genai = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@dataclass
class CacheStats:
    """Track prompt caching effectiveness."""

    requests: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cache_hit_rate(self) -> float:
        total_cached = self.cache_creation_tokens + self.cache_read_tokens
        if total_cached == 0:
            return 0.0
        return self.cache_read_tokens / total_cached

    def to_dict(self) -> dict:
        return {
            "requests": self.requests,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_hit_rate": round(self.cache_hit_rate, 3),
        }


def _system_prompt(language: str) -> str:
    """Build the system prompt for a given target language."""
    if language == "geno":
        return (
            "You are an expert Geno programmer. "
            "Generate correct, idiomatic Geno code.\n\n"
            f"{GENOTYPE_SPEC}\n\n"
            "Return ONLY the code inside a single code block. No explanations."
        )
    return (
        f"You are an expert {language} programmer. "
        f"Generate correct, idiomatic {language} code.\n\n"
        "Return ONLY the code inside a single code block. No explanations."
    )


def _retry(
    fn: Callable[[], T],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> T:
    """Call *fn* with exponential backoff on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            # Treat rate-limit (429) and server errors (5xx) as retryable.
            status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            retryable = (
                status in (429, 500, 502, 503, 529)
                or "rate" in str(exc).lower()
                or "overloaded" in str(exc).lower()
            )
            if not retryable or attempt == max_retries:
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                "Retryable error (attempt %d/%d), waiting %.1fs: %s",
                attempt + 1,
                max_retries,
                delay,
                exc,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Anthropic (Claude)
# ---------------------------------------------------------------------------

# Claude models that removed sampling parameters (`temperature`, `top_p`,
# `top_k`); requests that include them are rejected with a 400.
_CLAUDE_NO_SAMPLING_PREFIXES = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-fable",
    "claude-mythos",
)


@dataclass
class AnthropicClient:
    """Claude client with prompt caching for Geno experiments."""

    model: str = "claude-sonnet-4-6"
    max_tokens: int = 2048
    temperature: float = 0.0
    max_retries: int = 3
    cache_stats: CacheStats = field(default_factory=CacheStats)
    _client: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if anthropic is None:
            raise ImportError("anthropic package required: pip install anthropic")
        self._client = anthropic.Anthropic()

    def generate(self, model: str, prompt: str, language: str) -> str:
        """Generate a solution. Compatible with ExperimentRunner.set_generator()."""
        system_blocks = self._build_system(language)

        def _call() -> Any:
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": self.max_tokens,
                "system": system_blocks,
                "messages": [{"role": "user", "content": prompt}],
            }
            if not model.startswith(_CLAUDE_NO_SAMPLING_PREFIXES):
                kwargs["temperature"] = self.temperature
            return self._client.messages.create(**kwargs)

        response = _retry(_call, max_retries=self.max_retries)

        # Track cache stats
        usage = response.usage
        self.cache_stats.requests += 1
        self.cache_stats.input_tokens += usage.input_tokens
        self.cache_stats.output_tokens += usage.output_tokens
        if hasattr(usage, "cache_creation_input_tokens"):
            self.cache_stats.cache_creation_tokens += (
                usage.cache_creation_input_tokens or 0
            )
        if hasattr(usage, "cache_read_input_tokens"):
            self.cache_stats.cache_read_tokens += usage.cache_read_input_tokens or 0

        return cast(str, response.content[0].text)

    def _build_system(self, language: str) -> list[dict]:
        """Build system prompt with caching for the Geno spec."""
        blocks = []
        if language == "geno":
            blocks.append(
                {
                    "type": "text",
                    "text": (
                        "You are an expert Geno programmer. "
                        "Generate correct, idiomatic Geno code.\n\n"
                        f"{GENOTYPE_SPEC}"
                    ),
                    "cache_control": {"type": "ephemeral"},
                }
            )
        else:
            blocks.append(
                {
                    "type": "text",
                    "text": (
                        f"You are an expert {language} programmer. "
                        f"Generate correct, idiomatic {language} code."
                    ),
                }
            )
        blocks.append(
            {
                "type": "text",
                "text": "Return ONLY the code inside a single code block. No explanations.",
            }
        )
        return blocks


# ---------------------------------------------------------------------------
# OpenAI (GPT-5.x, etc.)
# ---------------------------------------------------------------------------

# OpenAI reasoning-class models only accept the default temperature; an
# explicit value is rejected.
_OPENAI_FIXED_TEMPERATURE_PREFIXES = ("gpt-5", "o1", "o3")


@dataclass
class OpenAIClient:
    """OpenAI client for GPT models."""

    model: str = "gpt-5.4"
    max_tokens: int = 2048
    temperature: float = 0.0
    max_retries: int = 3
    cache_stats: CacheStats = field(default_factory=CacheStats)
    _client: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if openai is None:
            raise ImportError("openai package required: pip install openai")
        self._client = openai.OpenAI()

    def generate(self, model: str, prompt: str, language: str) -> str:
        """Generate a solution. Compatible with ExperimentRunner.set_generator()."""
        system = _system_prompt(language)

        def _call() -> Any:
            kwargs: dict[str, Any] = {
                "model": model,
                # `max_tokens` is rejected by GPT-5-class and o-series models;
                # `max_completion_tokens` is the supported equivalent.
                "max_completion_tokens": self.max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            }
            if not model.startswith(_OPENAI_FIXED_TEMPERATURE_PREFIXES):
                kwargs["temperature"] = self.temperature
            return self._client.chat.completions.create(**kwargs)

        response = _retry(_call, max_retries=self.max_retries)

        usage = response.usage
        self.cache_stats.requests += 1
        if usage:
            self.cache_stats.input_tokens += usage.prompt_tokens or 0
            self.cache_stats.output_tokens += usage.completion_tokens or 0

        return cast(str, response.choices[0].message.content)


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------


@dataclass
class GeminiClient:
    """Google Gemini client."""

    model: str = "gemini-2.5-pro"
    max_tokens: int = 2048
    temperature: float = 0.0
    max_retries: int = 3
    cache_stats: CacheStats = field(default_factory=CacheStats)
    _configured: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if genai is None:
            raise ImportError(
                "google-generativeai package required: pip install google-generativeai"
            )
        if not self._configured:
            # genai.configure() reads GOOGLE_API_KEY from env by default
            genai.configure()
            self._configured = True

    def generate(self, model: str, prompt: str, language: str) -> str:
        """Generate a solution. Compatible with ExperimentRunner.set_generator()."""
        system = _system_prompt(language)

        def _call() -> Any:
            gmodel = genai.GenerativeModel(
                model_name=model,
                system_instruction=system,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=self.max_tokens,
                    temperature=self.temperature,
                ),
            )
            return gmodel.generate_content(prompt)

        response = _retry(_call, max_retries=self.max_retries)

        self.cache_stats.requests += 1
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            meta = response.usage_metadata
            self.cache_stats.input_tokens += getattr(meta, "prompt_token_count", 0)
            self.cache_stats.output_tokens += getattr(meta, "candidates_token_count", 0)

        return cast(str, response.text)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

LLMClientType = AnthropicClient | OpenAIClient | GeminiClient
LLMClientClass = type[AnthropicClient] | type[OpenAIClient] | type[GeminiClient]

# Maps model-name prefixes to provider constructors
_PROVIDER_PREFIXES: list[tuple[str, LLMClientClass]] = [
    ("claude-", AnthropicClient),
    ("gpt-", OpenAIClient),
    ("o1", OpenAIClient),
    ("o3", OpenAIClient),
    ("gemini-", GeminiClient),
]


def create_client(
    model: str,
    *,
    max_tokens: int = 2048,
    temperature: float = 0.0,
    max_retries: int = 3,
) -> LLMClientType:
    """Create the right LLM client for a model name.

    Raises ValueError if the model name doesn't match any known provider.
    """
    for prefix, cls in _PROVIDER_PREFIXES:
        if model.startswith(prefix):
            return cast(
                LLMClientType,
                cls(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    max_retries=max_retries,
                ),
            )
    raise ValueError(
        f"Unknown model provider for {model!r}. "
        f"Expected a name starting with: "
        f"{', '.join(p for p, _ in _PROVIDER_PREFIXES)}"
    )


# Keep backward-compatible alias
LLMClient = AnthropicClient
