"""Boot the server with a custom providers directory and check custom providers load."""

from __future__ import annotations

import json
import logging
import pathlib
import sys

import pytest

import music_assistant.providers as providers_package
from music_assistant.helpers.util import load_provider_module
from tests.conftest import full_mass_context

DOMAIN = "zz_custom_demo"

MANIFEST: dict[str, object] = {
    "type": "plugin",
    "domain": DOMAIN,
    "name": "Custom Demo",
    "description": "A custom provider used in tests.",
    "codeowners": ["@music-assistant"],
    "requirements": [],
}

MODULE = '''"""Custom demo provider."""

from music_assistant.models.plugin import PluginProvider


async def setup(mass, manifest, config):
    """Initialize provider(instance) with given configuration."""
    return PluginProvider(mass, manifest, config, set())
'''


def _seed_provider(custom_dir: pathlib.Path, folder: str, manifest: dict[str, object]) -> None:
    path = custom_dir / folder
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps(manifest))
    (path / "__init__.py").write_text(MODULE)
    (path / "strings.json").write_text(json.dumps({"settings": {"hello": {"label": "Hi"}}}))


@pytest.fixture(autouse=True)
def _restore_providers_path() -> object:
    """Undo the custom providers path registration after each test."""
    original = list(providers_package.__path__)
    yield
    providers_package.__path__[:] = original


async def test_custom_provider_is_discovered(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A provider in <data dir>/custom_providers loads like a built-in one."""
    custom_dir = tmp_path / "data" / "custom_providers"
    _seed_provider(custom_dir, DOMAIN, MANIFEST)
    # a custom provider may never shadow a built-in one
    _seed_provider(custom_dir, "builtin", {**MANIFEST, "domain": "builtin"})

    caplog.set_level(logging.WARNING)
    async with full_mass_context(tmp_path) as mass:
        manifests = {m.domain: m for m in mass.get_provider_manifests()}
        assert DOMAIN in manifests
        assert mass.custom_provider_paths == {DOMAIN: str(custom_dir / DOMAIN)}
        # the built-in provider is untouched
        assert "builtin" not in mass.custom_provider_paths
        assert manifests["builtin"].name != "Custom Demo"
        # importable through the regular provider import path
        await load_provider_module(DOMAIN, [])
        module = sys.modules[f"music_assistant.providers.{DOMAIN}"]
        assert module.__file__ == str(custom_dir / DOMAIN / "__init__.py")
        # its strings were merged into the translation catalog
        assert mass.translations.get_translation(f"provider.{DOMAIN}.settings.hello.label") == "Hi"

    assert any("Loaded custom provider zz_custom_demo" in r.message for r in caplog.records)
    assert any("Not loading custom provider builtin" in r.message for r in caplog.records)


async def test_no_custom_providers_directory(tmp_path: pathlib.Path) -> None:
    """Without the directory nothing changes."""
    async with full_mass_context(tmp_path) as mass:
        assert mass.custom_provider_paths == {}
        assert str(tmp_path / "data" / "custom_providers") not in providers_package.__path__
