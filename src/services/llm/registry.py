"""Model registry: preference order doubles as the fallback order.

Factories stay lazy so importing this module never needs provider keys or
packages. A model that cannot be built (missing key or package) is skipped
at call time; the service raises when nothing is buildable.
"""

from collections.abc import Callable, Mapping, Sequence
from functools import partial

from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from src.core.config import get_settings

DEFAULT_OPENAI = "default-openai"
FALLBACK_ANTHROPIC = "fallback-anthropic"
FALLBACK_GOOGLE = "fallback-google"

MODEL_ORDER: tuple[str, ...] = (DEFAULT_OPENAI, FALLBACK_ANTHROPIC, FALLBACK_GOOGLE)


def build_model(name: str) -> BaseChatModel:
    """Build one registry model from settings, failing fast when unconfigured."""
    settings = get_settings()
    if name == DEFAULT_OPENAI:
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model="gpt-4o-mini", api_key=SecretStr(settings.OPENAI_API_KEY))
    if name == FALLBACK_ANTHROPIC:
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model_name="claude-sonnet-4-5-20250929",
            api_key=SecretStr(settings.ANTHROPIC_API_KEY),
            # Aliased fields (timeout, stop) are required init kwargs in type stubs.
            timeout=None,
            stop=None,
        )
    if name == FALLBACK_GOOGLE:
        if not settings.GOOGLE_API_KEY:
            raise RuntimeError("GOOGLE_API_KEY is not set")
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model="gemini-2.5-flash", google_api_key=SecretStr(settings.GOOGLE_API_KEY)
        )
    raise RuntimeError(f"Unknown model {name!r}, expected one of {list(MODEL_ORDER)}")


def default_factories() -> Mapping[str, Callable[[], BaseChatModel]]:
    """Return one lazy factory per registry model, in preference order."""
    return {name: partial(build_model, name) for name in MODEL_ORDER}


def ordered_names(start: str | None, names: Sequence[str] | None = None) -> list[str]:
    """Return registry names circularly, starting at start or the default."""
    ordered = list(names if names is not None else MODEL_ORDER)
    if not ordered:
        return []
    if start is None:
        start = get_settings().DEFAULT_LLM_MODEL
    try:
        offset = ordered.index(start)
    except ValueError:
        offset = 0
    return ordered[offset:] + ordered[:offset]
