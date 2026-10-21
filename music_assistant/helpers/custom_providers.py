"""
Discovery of custom (external) providers.

A custom provider is a provider folder - laid out exactly like a built-in one under
``music_assistant/providers/<domain>`` - that lives in the custom providers directory
instead of inside the package. The directory is appended to the search path of the
``music_assistant.providers`` package, so custom providers import through the same
``music_assistant.providers.<domain>`` path as built-in ones and every loader that
imports by that path (provider module, setup flow) works unchanged.

Everything here is synchronous and free of server state so it can be unit tested in
isolation; callers run the filesystem work in a thread.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# name of the custom providers folder inside the data directory (the default location)
CUSTOM_PROVIDERS_DIRNAME = "custom_providers"
# environment variable that overrides the custom providers directory
CUSTOM_PROVIDERS_ENV = "MASS_CUSTOM_PROVIDERS_DIR"

# strings.json values of this form reference a shared string; they are resolved at runtime
# and never part of the source catalog (mirrors scripts/build_translations.py)
_REFERENCE_PATTERN = re.compile(r"^\[%key:(.+)%\]$")


@dataclass(frozen=True)
class CustomProviderDir:
    """A custom provider folder that passed discovery."""

    domain: str
    path: str


@dataclass(frozen=True)
class RejectedProviderDir:
    """A folder in the custom providers directory that was not loaded, and why."""

    name: str
    path: str
    reason: str


def resolve_custom_providers_path(data_dir: str, configured: str | None = None) -> str:
    """
    Return the custom providers directory.

    Precedence: an explicitly configured path (CLI), then the MASS_CUSTOM_PROVIDERS_DIR
    environment variable, then ``<data_dir>/custom_providers``.

    :param data_dir: The server's data (storage) directory.
    :param configured: Path given on the command line, if any.
    """
    path = configured or os.environ.get(CUSTOM_PROVIDERS_ENV)
    return str(Path(path).absolute()) if path else os.path.join(data_dir, CUSTOM_PROVIDERS_DIRNAME)


def discover_custom_providers(
    custom_path: str,
    builtin_domains: set[str],
    include_private: bool = False,
) -> tuple[list[CustomProviderDir], list[RejectedProviderDir]]:
    """
    Find the loadable provider folders in the custom providers directory.

    A folder is loaded when it holds a manifest.json whose domain equals the folder name
    (the folder name is the import name) and that domain is not a built-in provider.

    :param custom_path: The custom providers directory (may not exist).
    :param builtin_domains: Folder names of the built-in providers, which can't be shadowed.
    :param include_private: Also load folders starting with an underscore (dev mode).
    """
    accepted: list[CustomProviderDir] = []
    rejected: list[RejectedProviderDir] = []
    if not Path(custom_path).is_dir():
        return accepted, rejected
    for name in sorted(os.listdir(custom_path)):  # noqa: PTH208
        path = os.path.join(custom_path, name)
        if name.startswith(".") or not Path(path).is_dir() or name == "__pycache__":
            continue
        if name.startswith("_") and not include_private:
            continue
        if not name.isidentifier():
            rejected.append(
                RejectedProviderDir(name, path, "folder name is not a valid identifier")
            )
            continue
        if name in builtin_domains:
            rejected.append(
                RejectedProviderDir(name, path, "a built-in provider already uses this domain")
            )
            continue
        manifest_file = os.path.join(path, "manifest.json")
        try:
            with open(manifest_file, "rb") as file:
                manifest_domain = json.loads(file.read()).get("domain")
        except FileNotFoundError:
            rejected.append(RejectedProviderDir(name, path, "no manifest.json"))
            continue
        except (OSError, ValueError, AttributeError) as err:
            rejected.append(RejectedProviderDir(name, path, f"unreadable manifest.json: {err}"))
            continue
        if manifest_domain != name:
            rejected.append(
                RejectedProviderDir(
                    name, path, f"manifest domain {manifest_domain!r} does not match folder name"
                )
            )
            continue
        accepted.append(CustomProviderDir(name, path))
    return accepted, rejected


def register_custom_providers_path(custom_path: str) -> None:
    """
    Make providers in the custom directory importable as ``music_assistant.providers.<x>``.

    The path is appended, so a built-in provider is always found first on a name clash.

    :param custom_path: The custom providers directory.
    """
    import music_assistant.providers as providers_package  # noqa: PLC0415

    if custom_path not in providers_package.__path__:
        providers_package.__path__.append(custom_path)


def load_provider_strings(provider_path: str, domain: str) -> dict[str, str]:
    """
    Return a provider's English strings as flat translation keys.

    Keys get the ``provider.<domain>.`` prefix the build script gives built-in providers;
    reference values are dropped as they are resolved at runtime.

    :param provider_path: The provider folder.
    :param domain: The provider domain.
    """
    strings_file = os.path.join(provider_path, "strings.json")
    if not Path(strings_file).is_file():
        return {}
    with open(strings_file, "rb") as file:
        data = json.loads(file.read())
    flat: dict[str, str] = {}
    if isinstance(data, dict):
        _flatten_into(data, f"provider.{domain}.", flat)
    return {key: value for key, value in flat.items() if not _REFERENCE_PATTERN.match(value)}


def _flatten_into(data: dict[str, Any], prefix: str, out: dict[str, str]) -> None:
    """Flatten a nested strings dict into dotted, prefixed keys with string leaves."""
    for key, value in data.items():
        full_key = f"{prefix}{key}"
        if isinstance(value, dict):
            _flatten_into(value, f"{full_key}.", out)
        elif isinstance(value, str):
            out[full_key] = value
