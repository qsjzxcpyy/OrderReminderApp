"""Secure local storage for SMTP authorization codes."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from ctypes import wintypes
from pathlib import Path

import keyring


SERVICE_NAME = "OrderReminderApp"
DEFAULT_SECRET_PATH = Path(__file__).resolve().parent.parent / "data" / "smtp_secret.dpapi"
CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _protect_secret(value: str, flags: int) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI is only available on Windows")
    data = value.encode("utf-8")
    input_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    input_blob = _DataBlob(len(data), input_buffer)
    output_blob = _DataBlob()
    crypt_protect = ctypes.windll.crypt32.CryptProtectData
    crypt_protect.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob), wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt_protect.restype = wintypes.BOOL
    if not crypt_protect(ctypes.byref(input_blob), "OrderReminderApp", None, None, None, flags, ctypes.byref(output_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)


def protect_secret(value: str) -> bytes:
    """Encrypt a value with Windows DPAPI, including restricted sessions."""
    try:
        return _protect_secret(value, CRYPTPROTECT_UI_FORBIDDEN)
    except Exception as user_scope_error:
        try:
            return _protect_secret(value, CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE)
        except Exception:
            raise user_scope_error


def unprotect_secret(value: bytes) -> str:
    """Decrypt a DPAPI value for the current Windows user."""
    if os.name != "nt":
        raise RuntimeError("DPAPI is only available on Windows")
    input_buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    input_blob = _DataBlob(len(value), input_buffer)
    output_blob = _DataBlob()
    crypt_unprotect = ctypes.windll.crypt32.CryptUnprotectData
    crypt_unprotect.argtypes = [ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob), wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt_unprotect.restype = wintypes.BOOL
    if not crypt_unprotect(ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)


class CredentialStore:
    """Use Windows Credential Manager, with a DPAPI-encrypted local fallback."""

    def __init__(self, secret_path: str | Path = DEFAULT_SECRET_PATH):
        self.secret_path = Path(secret_path)

    def _read_fallback(self) -> dict[str, str]:
        if not self.secret_path.exists():
            return {}
        try:
            stored = json.loads(unprotect_secret(self.secret_path.read_bytes()))
            return stored if isinstance(stored, dict) else {}
        except Exception:
            return {}

    def _write_fallback(self, values: dict[str, str]) -> bool:
        try:
            self.secret_path.parent.mkdir(parents=True, exist_ok=True)
            encrypted = protect_secret(json.dumps(values, ensure_ascii=True, separators=(",", ":")))
            with tempfile.NamedTemporaryFile(dir=self.secret_path.parent, prefix=".smtp_secret.", delete=False) as temporary:
                temporary.write(encrypted)
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self.secret_path)
            return True
        except Exception:
            try:
                if "temporary_path" in locals():
                    temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def set(self, name: str, value: str) -> bool:
        if not value:
            return self.delete(name)
        try:
            keyring.set_password(SERVICE_NAME, name, value)
            values = self._read_fallback()
            if name in values:
                values.pop(name)
                self._write_fallback(values)
            return True
        except Exception:
            values = self._read_fallback()
            values[name] = value
            return self._write_fallback(values)

    def get(self, name: str) -> str | None:
        try:
            value = keyring.get_password(SERVICE_NAME, name)
            if value:
                return value
        except Exception:
            pass
        return self._read_fallback().get(name)

    def exists(self, name: str) -> bool:
        return bool(self.get(name))

    def delete(self, name: str) -> bool:
        keyring_deleted = True
        try:
            keyring.delete_password(SERVICE_NAME, name)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception:
            keyring_deleted = False
        values = self._read_fallback()
        values.pop(name, None)
        fallback_deleted = not values or self._write_fallback(values)
        return keyring_deleted and fallback_deleted


_default_store = CredentialStore()


def save_secret(name: str, value: str) -> bool:
    return _default_store.set(name, value)


def get_secret(name: str) -> str | None:
    return _default_store.get(name)


def delete_secret(name: str) -> bool:
    return _default_store.delete(name)
