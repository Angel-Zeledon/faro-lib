"""Maps a provider name to its credential fields and its implementation class."""
from backend.integrations.alegra import AlegraProvider
from backend.integrations.base import AccountingProvider
from backend.integrations.siigo import SiigoProvider

# The credential fields the UI must collect for each supported provider.
SUPPORTED_PROVIDERS = {
    "alegra": {"fields": ["email", "token"]},
    "siigo": {"fields": ["partner_id", "username", "access_key"]},
}

_PROVIDER_CLASSES = {
    "alegra": AlegraProvider,
    "siigo": SiigoProvider,
}


def get_provider(name: str, credentials: dict) -> AccountingProvider:
    """Instantiate the provider implementation for `name` with `credentials`.

    Raises ValueError if `name` is not a supported provider.
    """
    provider_class = _PROVIDER_CLASSES.get(name)
    if provider_class is None:
        raise ValueError(f"Unsupported accounting provider: {name!r}")
    return provider_class(credentials)
