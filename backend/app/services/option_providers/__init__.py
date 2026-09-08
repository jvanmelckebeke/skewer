"""Option provider registry — resolves a provider name to a singleton."""

from app.config import settings
from app.services.option_providers.base import (
    ChainUnavailable,
    OptionProvider,
    RawChain,
    RawOptionQuote,
)
from app.services.option_providers.yahoo import YahooOptionProvider

__all__ = [
    "ChainUnavailable", "OptionProvider", "RawChain", "RawOptionQuote",
    "init_option_provider", "get_option_provider",
]

_PROVIDERS: dict[str, type[OptionProvider]] = {
    "yahoo": YahooOptionProvider,
}

_instance: OptionProvider | None = None


def init_option_provider() -> None:
    """Instantiate the configured provider (called once at startup)."""
    global _instance
    name = settings.option_provider
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown option provider: {name!r}. Available: {list(_PROVIDERS)}")
    _instance = cls()


def get_option_provider() -> OptionProvider:
    if _instance is None:
        raise RuntimeError("Option provider not initialized — call init_option_provider() first")
    return _instance
