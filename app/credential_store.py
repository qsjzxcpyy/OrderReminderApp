"""OS credential storage for SMTP authorization codes."""

from __future__ import annotations

import keyring


SERVICE_NAME = "OrderReminderApp"


def save_secret(name: str, value: str) -> bool:
    """Save a named secret without placing it in SQLite."""
    if not value:
        delete_secret(name)
        return True
    try:
        keyring.set_password(SERVICE_NAME, name, value)
        return True
    except Exception:
        return False


def get_secret(name: str) -> str | None:
    try:
        return keyring.get_password(SERVICE_NAME, name)
    except Exception:
        return None


def delete_secret(name: str) -> bool:
    try:
        keyring.delete_password(SERVICE_NAME, name)
        return True
    except keyring.errors.PasswordDeleteError:
        return True
    except Exception:
        return False


class CredentialStore:
    """Compatible with the existing OrderService secret-store interface."""

    def set(self, name: str, value: str) -> None:
        save_secret(name, value)

    def get(self, name: str) -> str | None:
        return get_secret(name)

    def exists(self, name: str) -> bool:
        return bool(get_secret(name))

    def delete(self, name: str) -> None:
        delete_secret(name)
