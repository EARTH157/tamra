"""API keys live only in the OS credential store (Windows Credential Manager), never in files.

Entries use service "Tamra" and the provider name as the user name. Keys are never logged.
"""

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "Tamra"


def get_api_key(provider: str) -> str | None:
    return keyring.get_password(SERVICE, provider) or None


def set_api_key(provider: str, key: str) -> None:
    if not key:
        raise ValueError("an API key cannot be empty")
    keyring.set_password(SERVICE, provider, key)


def delete_api_key(provider: str) -> None:
    """Remove the stored key; does nothing when there is none."""
    try:
        keyring.delete_password(SERVICE, provider)
    except PasswordDeleteError:
        pass
