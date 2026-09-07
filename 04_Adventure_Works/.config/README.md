# `.config/` — project secrets

Everything in this directory is **per project**. Two projects on one machine do
not share credentials, and no secret sits beside a spec where it could be
committed by accident.

| File | Committed | Purpose |
|---|---|---|
| `.env.example` | yes | The template. Documents every variable and what it is for. |
| `.env` | **no** | Real values. Gitignored. |
| anything else here | **no** | The `.gitignore` ignores the whole directory except the two files above. |

## Setup

```bash
cp .config/.env.example .config/.env
```

## How it is read

`framework/deploy/_secrets.py::load_project_env` walks up from the working
directory looking for `.config/.env`, or reads `$FABRIC_PROJECT` if set. It is
called lazily on the first `resolve()`, so scripts do not have to load it
themselves.

**An existing environment variable always wins.** CI injects secrets as
environment variables, and a file checked out beside the specs must not
silently override what the runner set.

## What does not go here

Specs never hold a secret — they hold a reference, `${VAR}` or
`keyvault://vault/name`. `validate.py::check_secrets` fails the build on a
literal.
