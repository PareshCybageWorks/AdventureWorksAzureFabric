"""
Resolve secret REFERENCES from specs into secret values.

Specs never hold a secret. They hold a reference:

    client_secret_ref: keyvault://techtonic-kv/fabric-sp-secret
    webhook_ref:       keyvault://techtonic-kv/teams-webhook
    target:            ${AZURE_CLIENT_SECRET}

`check_secrets` in validate.py enforces that -- a literal value fails the build.
Until now nothing RESOLVED them, so the references were documentation: correct,
reviewed, and read by no code at all. This is the resolver.

Order of resolution
-------------------
Environment variables are tried BEFORE Key Vault, deliberately:

  - CI already injects secrets as environment variables from GitHub, and a
    round trip to Key Vault from a runner adds a network dependency and a
    second identity to configure for no benefit.
  - A developer can override locally without touching a shared vault.
  - Key Vault is the durable store and the fallback, which is what makes it the
    thing to update when a secret rotates.

Nothing here logs a secret value, and nothing writes one to a file. Values are
returned to the caller and go no further.

Usage:
    from _secrets import resolve
    secret = resolve("keyvault://techtonic-kv/fabric-sp-secret")
    secret = resolve("${AZURE_CLIENT_SECRET}")
"""

from __future__ import annotations

import os
import re
from pathlib import Path

KEYVAULT = re.compile(r"^keyvault://([^/]+)/(.+)$")
ENV = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$|^env:([A-Za-z_][A-Za-z0-9_]*)$")

# Per-project secret file. Every project carries a `.config/` directory holding
# its own .env, so two projects on one machine do not share one set of
# credentials and a secret is never placed beside a spec where it could be
# committed by accident.
CONFIG_DIR = ".config"
ENV_FILENAME = ".env"

# Conventional environment variable per secret name, so a Key Vault reference
# can be satisfied from the environment without the caller knowing which.
# `fabric-sp-secret` -> `FABRIC_SP_SECRET`.
_WELL_KNOWN = {
    "fabric-sp-secret": ("AZURE_CLIENT_SECRET", "FABRIC_CLIENT_SECRET"),
}

_cache: dict[str, str] = {}
_env_loaded = False


class SecretNotFound(RuntimeError):
    """Raised rather than returning None.

    A missing secret that resolves to None is passed onward and fails later as
    an authentication error against the wrong thing -- so it fails here, naming
    the reference that could not be resolved.
    """


def load_project_env(project: str | os.PathLike | None = None) -> Path | None:
    """Load `<project>/.config/.env` into the environment.

    Without this the `.config` directory is decorative: `resolve` reads
    os.getenv, so a .env file nobody loads resolves to nothing and the failure
    surfaces as an authentication error rather than a missing file.

    An EXISTING environment variable always wins. That preserves the resolution
    order this module documents -- CI injects secrets as environment variables,
    and a file checked out beside the specs must not silently override what the
    runner set. It also means `SERVICENOW_USER=x python deploy.py` behaves the
    way anyone would expect.

    Returns the file it loaded, or None. Missing is not an error: a project
    resolving everything from the environment has no .env at all.
    """
    roots: list[Path] = []
    if project is not None:
        roots.append(Path(project))
    elif os.getenv("FABRIC_PROJECT"):
        roots.append(Path(os.environ["FABRIC_PROJECT"]))
    else:
        # Walk up from the working directory so a script run from inside a
        # project folder finds that project's secrets rather than none.
        here = Path.cwd()
        roots.extend([here, *here.parents])

    for root in roots:
        path = root / CONFIG_DIR / ENV_FILENAME
        if not path.is_file():
            continue

        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            # `export FOO=bar` is common in hand-written .env files.
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)

        return path

    return None


def _env_candidates(name: str) -> list[str]:
    explicit = list(_WELL_KNOWN.get(name, ()))
    derived = name.replace("-", "_").replace(".", "_").upper()
    return explicit + [derived]


def _from_keyvault(vault: str, name: str) -> str | None:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError:
        # azure-keyvault-secrets is optional: a project resolving everything
        # from the environment should not need it installed.
        return None

    try:
        client = SecretClient(vault_url=f"https://{vault}.vault.azure.net/",
                              credential=DefaultAzureCredential())
        return client.get_secret(name).value
    except Exception:                                        # noqa: BLE001
        # Deliberately not re-raised with detail: an exception from the Key
        # Vault SDK can carry the request body. The caller gets SecretNotFound
        # naming the reference instead.
        return None


def resolve(reference: str, *, required: bool = True) -> str | None:
    """Resolve a spec reference to a secret value.

    Accepts `keyvault://vault/name`, `${ENV_VAR}` and `env:ENV_VAR`. Anything
    else is returned unchanged -- a spec field may legitimately hold a plain
    value, such as a vault name.
    """
    if not isinstance(reference, str):
        return reference
    if reference in _cache:
        return _cache[reference]

    # Lazily, once per process. Doing it on import would run before a caller
    # had a chance to chdir into the project or set FABRIC_PROJECT.
    global _env_loaded
    if not _env_loaded:
        _env_loaded = True
        load_project_env()

    env_match = ENV.match(reference)
    if env_match:
        variable = env_match.group(1) or env_match.group(2)
        value = os.getenv(variable)
        if value:
            _cache[reference] = value
            return value
        if required:
            raise SecretNotFound(
                f"{reference} is not set. Export {variable}, or supply it as a "
                f"repository secret if this is running in CI.")
        return None

    vault_match = KEYVAULT.match(reference)
    if vault_match:
        vault, name = vault_match.group(1), vault_match.group(2)

        # Environment first -- see the module docstring.
        for variable in _env_candidates(name):
            value = os.getenv(variable)
            if value:
                _cache[reference] = value
                return value

        value = _from_keyvault(vault, name)
        if value:
            _cache[reference] = value
            return value

        if required:
            raise SecretNotFound(
                f"could not resolve {reference}.\n"
                f"  Tried environment: {', '.join(_env_candidates(name))}\n"
                f"  Then Key Vault '{vault}', secret '{name}'.\n"
                f"  Either export one of those variables, or confirm the secret "
                f"exists and that this identity has 'Key Vault Secrets User' on "
                f"the vault.")
        return None

    # Not a reference: a literal. Returned as-is; validate.py is what stops a
    # literal SECRET being committed in the first place.
    return reference


def describe(reference: str) -> str:
    """Where a reference would resolve from, without resolving it.

    For diagnostics, so someone can be told why authentication failed without a
    secret value appearing in a terminal or a CI log.
    """
    env_match = ENV.match(reference or "")
    if env_match:
        variable = env_match.group(1) or env_match.group(2)
        return f"environment {variable} ({'set' if os.getenv(variable) else 'NOT set'})"

    vault_match = KEYVAULT.match(reference or "")
    if vault_match:
        vault, name = vault_match.group(1), vault_match.group(2)
        found = next((v for v in _env_candidates(name) if os.getenv(v)), None)
        if found:
            return f"environment {found} (set) — Key Vault not consulted"
        return f"Key Vault {vault}/{name} (no matching environment variable)"

    return "a literal value, not a reference"
