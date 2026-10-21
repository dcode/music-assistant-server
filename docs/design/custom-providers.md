# Design: custom (external) providers

Status: draft / proposal · Branch: `feat/custom-providers` (dcode/music-assistant-server)

## Problem

Music Assistant can only load providers that ship inside the `music_assistant` package.
Three places hard-code that assumption:

| Where | What it assumes |
|---|---|
| `mass.py` `__load_provider_manifests` | manifests are only discovered under `PROVIDERS_PATH` (`music_assistant/providers`) |
| `helpers/util.py` `load_provider_module` | the module is imported as `music_assistant.providers.<domain>` |
| `controllers/config/flows.py` | a setup flow is imported as `music_assistant.providers.<domain>.setup_flow` |
| `controllers/translations` | provider strings are compiled into `translations/en.json` at build time |

So anyone who wants a provider for a niche service (a personal API client, a regional
broadcaster, a self-hosted app) must either get it merged upstream or maintain a fork of
the whole server and rebuild the image on every release. Upstream then carries every
niche provider forever, including the review, CI and support cost.

Home Assistant solved the same problem with `custom_components/`: a folder of
integrations in the user's config directory, loaded through the same machinery as
built-in ones, clearly flagged as unsupported. Music Assistant's provider model is close
enough to Home Assistant's that the same approach fits.

## Goals

- Load a provider from a folder outside the package, with **no change to the provider
  API**. A custom provider is written exactly like a built-in one (manifest.json,
  `setup()`, optional `setup_flow.py`, `strings.json`, icons).
- **No behaviour change** when the folder is absent or empty.
- Built-in providers always win: a custom provider can't shadow or replace one.
- Custom providers never load in safe mode, so a broken one can always be recovered from.
- Work on every install method, including the Home Assistant add-on, where the user has
  no access to the add-on's `/data`.

## Non-goals (for now)

- An in-app store / one-click install (HACS-style). Possible later on top of this.
- Sandboxing. Like Home Assistant custom components, custom providers run with full
  server privileges; that is the user's trust decision, made visible with a warning.
- A stable, versioned provider API. Custom providers track the server like built-in
  ones do; breakage on upgrade is expected and logged.

## Design

### 1. Discovery

A new *custom providers directory*, resolved in this order:

1. `--custom-providers-dir` CLI argument
2. `MASS_CUSTOM_PROVIDERS_DIR` environment variable
3. default: `<data dir>/custom_providers`

Each subfolder is a provider, exactly like `music_assistant/providers/<domain>/`.
Discovery (new `helpers/custom_providers.py`, pure and unit-testable):

- skips hidden (`.`) folders, and `_` folders unless dev mode is on (same rule as built-ins);
- requires a `manifest.json` whose `domain` equals the folder name, because the folder
  name is the import name;
- refuses a domain that already exists as a built-in provider (logged as an error);
- skips everything in safe mode.

### 2. Import

At startup, before any provider loads, the custom directory is **appended** to
`music_assistant.providers.__path__`. Python then resolves
`music_assistant.providers.<domain>` (and `.setup_flow`) for custom providers through the
normal import system, so `load_provider_module`, the setup-flow loader and anything else
that imports by that path work unchanged. Appending (not prepending) is a second layer of
protection: even if the conflict check were bypassed, the built-in module is found first.

### 3. Requirements

Unchanged: `load_provider_module` already installs a manifest's pinned `requirements`
with `uv pip install` at load time. Custom providers get that for free.

### 4. Translations

`TranslationController.setup()` merges each custom provider's `strings.json` into the
English source catalog under the same `provider.<domain>.` prefix the build script uses.
Keys that already exist are never overwritten, and reference values (`[%key:…%]`) are
dropped exactly like the build script does, so the runtime fallback handles them.
Per-language files (`translations/<lang>.json` inside the provider) are a follow-up.

### 5. Icons

Unchanged: `detect_provider_icons()` takes the provider folder path and loads the icons
into memory, so it works for any folder.

### 6. Visibility

- Every custom provider logs a warning at startup, mirroring Home Assistant:
  *"Loaded custom provider X from PATH. Custom providers are not tested by Music
  Assistant; disable it if you experience issues."*
- `mass.custom_provider_domains` lists them, so diagnostics and issue templates can show
  that a custom provider was loaded.
- (Follow-up, needs `music-assistant/models`) a `custom: bool` field on `ProviderManifest`
  so the frontend can badge custom providers.

### 7. Home Assistant add-on

The add-on's `/data` is not reachable by users, so the add-on maps its add-on config
folder (`map: addon_config:rw`, visible as `/addon_configs/<slug>/` over Samba, SSH and
the File editor) and sets:

```yaml
environment:
  MASS_CUSTOM_PROVIDERS_DIR: /config/custom_providers
```

That is a two-line change in `music-assistant/home-assistant-addon`.

## Alternatives considered

| Option | Why not (first) |
|---|---|
| **Python entry points** (`pip install` a package that registers a provider) | Cleanest for packaged providers, but needs an install story inside the container (which already exists for requirements) and a UI to manage it. A good phase 2: entry-point packages can be discovered into the same registry this design adds. |
| **Make `music_assistant.providers` a namespace package** | Removing `__init__.py` changes import semantics for every built-in provider and tooling; extending `__path__` gets the same result with no risk. |
| **Fork + custom image** (today's only option) | Works (see dcode/music-assistant-calm), but every user rebuilds per release and the fork drifts. |
| **Bridge services** (expose a service as Subsonic/Jellyfin and use an existing provider) | No server change, but loses provider-specific browse/search/metadata and adds a service to run. |

## Risks

- **Support load.** Bug reports caused by custom providers. Mitigation: the warning, the
  `custom_provider_domains` diagnostic, and safe mode.
- **Dependency conflicts.** A custom provider's requirements could up/downgrade a shared
  package. Same risk exists for built-ins today; pinned requirements and the existing
  version check limit it. A follow-up could refuse requirements that conflict with the
  server's own pins.
- **API churn.** Internal provider APIs change between releases. Accepted, as for HA.

## Roadmap

| Phase | Scope | Where |
|---|---|---|
| **1** | Custom providers directory: discovery, import path, translations, warning, safe-mode skip, tests, docs | `server` (this branch) |
| **1b** | Add-on: map `addon_config`, set `MASS_CUSTOM_PROVIDERS_DIR` | `home-assistant-addon` |
| **2** | `custom` flag on `ProviderManifest` + frontend badge; diagnostics include custom providers | `models`, `frontend`, `server` |
| **3** | Entry-point discovery for pip-installable provider packages; per-language strings | `server` |
| **4** | Optional: install/update custom providers from a URL or index in the UI | `server`, `frontend` |

## Upstream engagement

Before opening a PR: start a GitHub discussion describing the problem and this design,
because it is a product decision (support burden) as much as a technical one. Phase 1 is
small and self-contained enough to propose as one PR once there is agreement.
