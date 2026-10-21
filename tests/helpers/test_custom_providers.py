"""Tests for the discovery of custom (external) providers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import music_assistant.providers as providers_package
from music_assistant.helpers.custom_providers import (
    CUSTOM_PROVIDERS_ENV,
    discover_custom_providers,
    load_provider_strings,
    register_custom_providers_path,
    resolve_custom_providers_path,
)

if TYPE_CHECKING:
    import pytest


def _make_provider(root: Path, folder: str, domain: str | None = None) -> Path:
    """Create a minimal provider folder with a manifest."""
    path = root / folder
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps({"domain": domain or folder}))
    return path


def test_resolve_path_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI value beats the environment variable, which beats the data dir default."""
    monkeypatch.delenv(CUSTOM_PROVIDERS_ENV, raising=False)
    assert resolve_custom_providers_path("/data") == "/data/custom_providers"
    monkeypatch.setenv(CUSTOM_PROVIDERS_ENV, str(tmp_path / "env"))
    assert resolve_custom_providers_path("/data") == str(tmp_path / "env")
    assert resolve_custom_providers_path("/data", str(tmp_path / "cli")) == str(tmp_path / "cli")


def test_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    """No custom providers directory means nothing to load."""
    assert discover_custom_providers(str(tmp_path / "absent"), set()) == ([], [])


def test_discovery_accepts_and_rejects(tmp_path: Path) -> None:
    """Only valid, non-conflicting provider folders are accepted."""
    _make_provider(tmp_path, "calm")
    _make_provider(tmp_path, "builtin")
    _make_provider(tmp_path, "renamed", domain="other")
    _make_provider(tmp_path, "_private")
    _make_provider(tmp_path, ".hidden")
    (tmp_path / "no_manifest").mkdir()
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "manifest.json").write_text("{not json")
    (tmp_path / "not-an-identifier").mkdir()
    (tmp_path / "a_file.py").write_text("")

    accepted, rejected = discover_custom_providers(str(tmp_path), {"builtin"})

    assert [p.domain for p in accepted] == ["calm"]
    assert accepted[0].path == str(tmp_path / "calm")
    reasons = {r.name: r.reason for r in rejected}
    assert set(reasons) == {"builtin", "renamed", "no_manifest", "broken", "not-an-identifier"}
    assert "built-in" in reasons["builtin"]
    assert "does not match" in reasons["renamed"]


def test_private_folders_load_in_dev_mode(tmp_path: Path) -> None:
    """Underscore folders follow the built-in rule: dev mode only."""
    _make_provider(tmp_path, "_demo")
    accepted, _ = discover_custom_providers(str(tmp_path), set(), include_private=True)
    assert [p.domain for p in accepted] == ["_demo"]


def test_custom_provider_imports_as_provider_module(tmp_path: Path) -> None:
    """A registered custom provider imports via music_assistant.providers.<domain>."""
    path = _make_provider(tmp_path, "zz_custom_test_provider")
    (path / "__init__.py").write_text("VALUE = 42\n")
    original_path = list(providers_package.__path__)
    try:
        register_custom_providers_path(str(tmp_path))
        register_custom_providers_path(str(tmp_path))  # idempotent
        assert providers_package.__path__.count(str(tmp_path)) == 1
        # built-in providers stay first on the search path
        assert providers_package.__path__[0] == original_path[0]
        import music_assistant.providers.zz_custom_test_provider as module  # noqa: PLC0415

        assert module.VALUE == 42
    finally:
        providers_package.__path__[:] = original_path


def test_load_provider_strings(tmp_path: Path) -> None:
    """strings.json is flattened under the provider prefix; references are dropped."""
    path = _make_provider(tmp_path, "calm")
    (path / "strings.json").write_text(
        json.dumps(
            {
                "manifest": {"description": "Calm content"},
                "media": {"recommendations": {"home": {"name": "For you"}}},
                "shared": "[%key:common::username%]",
            }
        )
    )
    assert load_provider_strings(str(path), "calm") == {
        "provider.calm.manifest.description": "Calm content",
        "provider.calm.media.recommendations.home.name": "For you",
    }
    assert load_provider_strings(str(tmp_path / "missing"), "missing") == {}
