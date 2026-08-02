"""Narrow privacy policy shared by persistent logs and support diagnostics."""

from __future__ import annotations

import getpass
import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sqlalchemy.engine import make_url

from app.core.settings import Settings

_UUID_PATTERN: Final = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)
_SENSITIVE_LABEL_PATTERN: Final = re.compile(
    r"(?i)\b(password|token|secret|api[_-]?key|authorization)"
    r"(\s*(?:=|:)\s*)(?!<REDACTED>)[^\s,;]+"
)
_CREDENTIAL_URL_PATTERN: Final = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@[^\s]+")
_URL_PATTERN: Final = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s]+")
_WINDOWS_PATH_PATTERN: Final = re.compile(
    r"(?i)(?<![\w])(?:[a-z]:[\\/](?:[^<>:\"|?*\r\n]+)|"
    r"\\\\[^\\/\s]+[\\/][^<>:\"|?*\r\n]+)"
)
_POSIX_USER_PATH_PATTERN: Final = re.compile(r"(?<![\w])/(?:home|Users)/[^/\s]+(?:/[^\s,;]*)?")
_POSIX_ABSOLUTE_PATH_PATTERN: Final = re.compile(r"(?<![\w<])/(?!/)[^\s,;]+")
_PERSISTED_STATE_ERROR_PATTERN: Final = re.compile(
    r"(?i)(Portfolio valuation rejected persisted state:)\s*.*"
)
_SELECTED_SENSITIVE_ENVIRONMENT_NAMES: Final = (
    "MIRA_DATABASE_URL",
    "MIRA_PASSWORD",
    "MIRA_TOKEN",
    "MIRA_SECRET",
    "MIRA_API_KEY",
    "MIRA_AUTHORIZATION",
    "DATABASE_URL",
    "PASSWORD",
    "TOKEN",
    "SECRET",
    "API_KEY",
    "AUTHORIZATION",
    "OPENAI_API_KEY",
)


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """Redact known secrets and user-specific locations with stable tokens."""

    replacements: tuple[tuple[str, str], ...]
    forbidden_values: tuple[str, ...]

    @classmethod
    def from_settings(cls, settings: Settings) -> RedactionPolicy:
        """Build a policy from narrowly selected local privacy inputs."""
        replacements: dict[str, str] = {}
        _add_path_replacements(replacements, settings.database_path, "<DATABASE_PATH>")
        _add_path_replacements(replacements, settings.database_directory, "<DATABASE_DIR>")
        _add_path_replacements(replacements, settings.data_directory, "<DATA_DIR>")
        _add_path_replacements(replacements, settings.cache_directory, "<CACHE_DIR>")
        _add_path_replacements(replacements, settings.export_directory, "<EXPORT_DIR>")
        _add_path_replacements(replacements, settings.backup_directory, "<BACKUP_DIR>")
        _add_path_replacements(replacements, settings.log_directory, "<LOG_DIR>")
        _add_path_replacements(replacements, Path.cwd(), "<CWD>")
        _add_path_replacements(replacements, Path.home(), "<HOME>")

        _add_replacement(replacements, settings.database_url, "<DATABASE_URL>")
        try:
            url = make_url(settings.database_url)
        except Exception:
            url = None
        if url is not None:
            _add_replacement(replacements, url.username, "<REDACTED>")
            _add_replacement(replacements, url.password, "<REDACTED>")

        for name in _SELECTED_SENSITIVE_ENVIRONMENT_NAMES:
            _add_replacement(replacements, os.environ.get(name), "<REDACTED>")
        _add_replacement(replacements, _safe_username(), "<USER>")
        _add_replacement(replacements, _safe_hostname(), "<HOST>")

        ordered = tuple(sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True))
        forbidden = tuple(value for value, _ in ordered if len(value) >= 3)
        return cls(replacements=ordered, forbidden_values=forbidden)

    def redact(self, text: str, *, identifiers: bool = False) -> str:
        """Return text with paths, URLs, secrets, and optional UUIDs removed."""
        redacted = text
        for value, placeholder in self.replacements:
            redacted = redacted.replace(value, placeholder)
            redacted = redacted.replace(value.casefold(), placeholder)
        redacted = _PERSISTED_STATE_ERROR_PATTERN.sub(r"\1 <ERROR>", redacted)
        redacted = _CREDENTIAL_URL_PATTERN.sub("<DATABASE_URL>", redacted)
        redacted = _URL_PATTERN.sub("<URL>", redacted)
        redacted = _SENSITIVE_LABEL_PATTERN.sub(r"\1\2<REDACTED>", redacted)
        redacted = _WINDOWS_PATH_PATTERN.sub("<PATH>", redacted)
        redacted = _POSIX_USER_PATH_PATTERN.sub("<PATH>", redacted)
        redacted = _POSIX_ABSOLUTE_PATH_PATTERN.sub("<PATH>", redacted)
        if identifiers:
            redacted = _UUID_PATTERN.sub("<IDENTIFIER>", redacted)
        return redacted

    def privacy_violation(self, text: str) -> str | None:
        """Return a sanitized violation category when private content remains."""
        for value in self.forbidden_values:
            if value in text or value.casefold() in text.casefold():
                return "known_private_value"
        if _CREDENTIAL_URL_PATTERN.search(text) or _URL_PATTERN.search(text):
            return "url"
        if (
            _WINDOWS_PATH_PATTERN.search(text)
            or _POSIX_USER_PATH_PATTERN.search(text)
            or _POSIX_ABSOLUTE_PATH_PATTERN.search(text)
        ):
            return "absolute_path"
        sensitive = _SENSITIVE_LABEL_PATTERN.search(text)
        if sensitive is not None:
            return "sensitive_label"
        return None


def _add_path_replacements(
    replacements: dict[str, str],
    path: Path,
    placeholder: str,
) -> None:
    absolute = Path(os.path.abspath(path))
    _add_replacement(replacements, str(absolute), placeholder)
    _add_replacement(replacements, absolute.as_posix(), placeholder)
    try:
        _add_replacement(replacements, absolute.as_uri(), placeholder)
    except ValueError:
        pass


def _add_replacement(
    replacements: dict[str, str],
    value: str | None,
    placeholder: str,
) -> None:
    if value is not None and len(value) >= 3:
        replacements[value] = placeholder


def _safe_username() -> str | None:
    try:
        return getpass.getuser()
    except Exception:
        return None


def _safe_hostname() -> str | None:
    try:
        return platform.node()
    except Exception:
        return None


__all__ = ["RedactionPolicy"]
