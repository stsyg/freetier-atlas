# Configuration System

FreeTier Atlas is configured through declarative, typed YAML. Configuration is
**validated before use** so a typo or malformed file fails fast with an
actionable, file-scoped error instead of silently mis-configuring a service.

This is slice 1 of the F003 epic (the domain model, immutable evidence history,
and Z0 classification arrive in later slices).

## Configuration families

Example files live under `config/examples/`:

| Family          | Example file                                   | Root keys                                |
| --------------- | ---------------------------------------------- | ---------------------------------------- |
| `application`   | `application.example.yaml`                     | `application`, `catalogue`, `admin`, `features` |
| `schedules`     | `schedules.example.yaml`                        | `schedules`                              |
| `llm-providers` | `llm-providers.example.yaml`                    | `llm`                                    |
| `provider`      | `providers/*.example.yaml`                       | `provider`, `sources`, `publishing`      |

The family is detected automatically from the top-level keys.

## Validating configuration

Validate every example config:

```powershell
pwsh -File scripts/validate-config.ps1
```

```bash
bash scripts/validate-config.sh
```

Validate specific files by passing paths:

```bash
bash scripts/validate-config.sh config/examples/application.example.yaml
```

The command exits `0` when every file is valid and non-zero when any file fails,
printing the offending file plus one actionable line per problem. Config
validation also runs as part of `scripts/test.ps1` / `scripts/test.sh`.

Under the hood the CLI is:

```bash
python -m app.config.cli validate <paths...>
python -m app.config.cli emit-schema <application|schedules|llm-providers|provider>
```

`emit-schema` prints the JSON Schema for a family (useful for editor tooling).

## What validation catches

- **Unknown fields** — every model forbids extra keys, so a typo names the exact
  field and file.
- **Malformed YAML** — reports the file with the line and column of the syntax
  error.
- **Missing required fields** — names the missing field path.
- **Type and range errors** — e.g. non-integer counts, out-of-range thresholds,
  malformed cron expressions, `automatic_threshold` below `uncertain_threshold`.
- **Inline secrets** — a raw credential key (`api_key`, `password`, `token`, …)
  anywhere in a file is rejected.

## Secrets are referenced, never stored

Configuration never contains secret **values**. A credential is referenced by
the **name** of an environment variable using an `*_env` field, for example:

```yaml
llm:
  providers:
    gemini:
      enabled: false
      api_key_env: GEMINI_API_KEY   # a variable name, not a value
```

The real value is supplied by the runtime environment (see `.env.example`) and
is never read or printed by the validator.
