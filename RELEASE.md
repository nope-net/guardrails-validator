# Releasing `nope-crisis-screen`

This validator wraps NOPE's hosted `/v1/evaluate` API. Two distribution channels:
**PyPI** (`pip install nope-crisis-screen`) and the **Guardrails Hub**
(`guardrails hub install hub://nope/crisis_screen`).

## Pre-flight (every release)

1. Bump the version in **both** `pyproject.toml` and `nope_crisis_screen/__init__.py`
   (the release workflow asserts the git tag equals `__version__`).
2. `make dev && make test` — unit tests (mocked) must pass.
3. `NOPE_API_KEY=nope_... make smoke` — **live** smoke test against production.
   This is the gate that matters: it proves the validator is wired to a working
   endpoint. (The v0.1.0 release shipped broken because it had only mocked tests
   pointing at the long-removed `/v1/screen`.)
4. Commit, open a PR, merge to `main`.

## PyPI (automated via GitHub Actions)

Publishing is done by `.github/workflows/release.yml` on a `v*` tag push, using
**PyPI Trusted Publishing** (OIDC — no API token stored anywhere).

One-time setup on PyPI:
1. Create the project owner / claim the `nope-crisis-screen` name.
2. PyPI → project → *Publishing* → add a **GitHub trusted publisher**:
   - Owner: `nope-net`, Repo: `guardrails-validator`
   - Workflow: `release.yml`, Environment: `pypi`
3. (Recommended) add the production `NOPE_API_KEY` as a repo **Actions secret** so
   the release workflow's live smoke test runs and gates the publish. Without it,
   the smoke step skips (does not block).

Cut a release:
```bash
git tag v0.2.0
git push origin v0.2.0
```
The workflow runs the live smoke test, then builds and publishes.

> Until the trusted publisher is configured, the workflow runs but the publish step
> fails — nothing is pushed to PyPI by accident.

## Guardrails Hub

The Hub is a separate, manually-reviewed channel. After the PyPI release:
1. Ensure `@register_validator(name="nope/crisis_screen", data_type="string")` and
   the package metadata are current.
2. Submit per the Guardrails Hub contributor process
   (https://www.guardrailsai.com/docs — "Create your own validator" / Hub submission).
   Hub listing is reviewed by the Guardrails team; it is not auto-published.

## Notes

- This is an **API-backed** validator (model runs on NOPE infra), so it requires a
  NOPE API key — the same pattern as Hub validators like Valid Address (Google Maps)
  or Bespoke MiniCheck (BespokeLabs), and Guardrails' own remote-inference validators.
- Failure philosophy: transient/server-side errors fail **open**; developer-side
  errors (bad key, wrong URL) fail **loud**. See README "Safety Design".
