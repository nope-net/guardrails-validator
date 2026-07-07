# NOPE Crisis Screen Validator

A [Guardrails AI](https://guardrailsai.com) validator for detecting mental health crises and safety risks in LLM inputs and outputs using [NOPE](https://nope.net). Backed by NOPE's Edge-classifier `/v1/evaluate` API.

- **Latency:** ~200-500ms per call
- **Cost:** $0.003 per call ($1 free credit for new accounts)
- **Coverage:** 9 risk types, localized crisis resources

## Installation

```bash
pip install nope-crisis-screen
```

Or via Guardrails Hub:

```bash
guardrails hub install hub://nope/crisis_screen
```

> **Note:** This validator calls a hosted API (NOPE `/v1/evaluate`) and therefore
> requires a NOPE API key — the same pattern as other API-backed Guardrails
> validators (e.g. Valid Address → Google Maps, Bespoke MiniCheck → BespokeLabs).
> The classifier model runs on NOPE's infrastructure, not locally.

## Requirements

- Python 3.9+
- A NOPE API key ([get one free](https://dashboard.nope.net))

## Safety Design

This validator **fails open on transient/server-side problems** — if the NOPE API is briefly unavailable, validation passes rather than blocking users, so the safety layer never becomes a denial-of-service vector. **Developer-side errors fail loud**, because a silently misconfigured safety layer is worse than none.

| Scenario | Behavior | Rationale |
|----------|----------|-----------|
| Network error | Pass (fail open) | Transient |
| API timeout | Pass (fail open) | Transient |
| Rate limited (429) | Pass (fail open) | Transient |
| Server error (5xx) | Pass (fail open) | Transient, server-side |
| Auth/balance error (401/402) | **Raise `ValueError`** | Bad key or empty balance — fix it |
| Other client error (400/404/…) | **Raise `ValueError`** | Misconfiguration (e.g. wrong `NOPE_API_URL`) |

## Quick Start

```python
import os
from guardrails import Guard
from nope_crisis_screen import CrisisScreen

# Set your API key
os.environ["NOPE_API_KEY"] = "nope_live_xxx"

# Create a guard. on_fail="noop" lets you inspect the outcome instead of raising;
# with the default on_fail, a failed validation raises ValidationError (see below).
guard = Guard().use(CrisisScreen(severity_threshold="moderate", on_fail="noop"))

# Screen user input
result = guard.validate("I've been feeling really hopeless lately")

if result.validation_passed:
    print("No concerning signals detected")
else:
    # Access failure details via validation_summaries
    for summary in result.validation_summaries:
        print(f"Failed: {summary.failure_reason}")
        # Metadata includes risks, resources, rationale
        print(f"Risks: {summary.metadata.get('risks')}")
```

## Regulatory Context

Several jurisdictions are introducing requirements around AI chatbots and mental-health crises. This validator provides crisis detection and resource surfacing — capabilities relevant to those obligations — but **using it does not, by itself, make you compliant with any law**. You are responsible for determining which laws apply to your product and whether your overall crisis-response protocols meet them; consult qualified legal counsel.

For a neutral, sourced overview of the regulatory landscape, see [nope.net/regs](https://nope.net/regs).

## Risk Types

| Risk Type | Description | Framework |
|-----------|-------------|-----------|
| `suicide` | Self-directed lethal intent | C-SSRS |
| `self_harm` | Non-suicidal self-injury | Clinical NSSI criteria |
| `self_neglect` | Self-care failure, eating disorders, substance crisis | - |
| `violence` | Risk of harm to others | HCR-20 |
| `abuse` | Physical, emotional, sexual, financial abuse | DASH |
| `sexual_violence` | Rape, sexual assault, coercion | - |
| `neglect` | Failure to care for dependents | Safeguarding frameworks |
| `exploitation` | Trafficking, grooming, sextortion | Trafficking indicators |
| `stalking` | Persistent unwanted contact, surveillance | - |

When risks are detected, the validator returns localized crisis resources (hotlines, chat services) for the user's country (set via `country` or per-call `metadata`).

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | `NOPE_API_KEY` env var | Your NOPE API key |
| `severity_threshold` | `str` | `"moderate"` | Minimum severity: `mild`, `moderate`, `high`, `critical` |
| `risk_types` | `list[str]` | All 9 types | Which risk types to check |
| `country` | `str` | `"US"` | ISO country code for localized resources |
| `include_resources` | `bool` | `True` | Include crisis resources in failure metadata |
| `include_recommended_reply` | `bool` | `False` | Attach a deterministic, resource-grounded safe reply as `fix_value` (no extra latency/cost). Auto-enabled by `on_fail="fix"` |
| `on_fail` | `str \| Callable` | `None` | Guardrails on_fail action |

> The severity scale is `mild → moderate → high → critical` (matching the NOPE API). `"low"` is accepted as a deprecated alias for `"mild"`.

## Examples

### Basic Input Screening

```python
from nope_crisis_screen import CrisisScreen

guard = Guard().use(CrisisScreen())

# This passes - no crisis signals
guard.validate("What's the weather like?")

# This fails - detects suicidal ideation
guard.validate("I've been thinking about ending it all")
```

### Filter Specific Risk Types

```python
# Only check for self-directed harm
guard = Guard().use(CrisisScreen(
    risk_types=["suicide", "self_harm", "self_neglect"],
    severity_threshold="mild",
))
```

### With Conversation Context

Passing recent conversation context improves accuracy:

```python
guard.validate(
    "I don't know what to do anymore",
    metadata={
        "messages": [
            {"role": "user", "content": "I've been struggling with thoughts of hurting myself"},
            {"role": "assistant", "content": "I'm concerned about what you're sharing..."},
            {"role": "user", "content": "I don't know what to do anymore"},
        ]
    }
)
```

### Localized Resources

```python
# Get UK crisis resources
guard = Guard().use(CrisisScreen(country="GB"))

# Or override per-call
guard.validate("...", metadata={"country": "AU"})
```

### Wrap LLM Calls

```python
import openai

# Recommended: validate user input before LLM call
guard = Guard().use(CrisisScreen(), on="messages")

response = guard(
    openai.chat.completions.create,
    model="gpt-4",
    messages=[{"role": "user", "content": user_message}],
)
```

Without `on="messages"`, the validator runs on the LLM output. This still works—we detect crisis signals in any text—but input validation is the primary use case.

## On-Fail Actions

All standard Guardrails on_fail actions are supported. **If you don't set `on_fail`, the default raises `ValidationError` on failure** (same as `exception`) — set `on_fail="noop"` if you want to inspect `validation_passed`/`validation_summaries` without raising.

| Action | Behavior | Use Case |
|--------|----------|----------|
| `exception` | Raise `ValidationError` | Hard stop, alert system |
| `noop` | Log but continue | Monitoring, analytics |
| `refrain` | Return `None` | Silent filtering |
| `fix` | Replace with safe response | Auto-respond to crisis with resources |
| `fix_reask` | Fix then reask if needed | Fallback chain |
| Custom function | Your handler | Route to human, show resources |

### Using `on_fail="fix"`

When you use `on_fail="fix"`, the validator returns a **deterministic, supportive safe reply** built from the matched crisis resource (e.g. the local hotline). It is intentionally *not* LLM-generated — a fixed, resource-grounded message has no hallucination risk, adds no latency, and costs nothing extra:

```python
guard = Guard().use(CrisisScreen(on_fail="fix"))

result = guard.validate("I've been thinking about ending it all")
# result.validated_output contains a safe, supportive response pointing to crisis resources
```

If the API returns no resources (e.g. `include_resources=False`), no `fix_value` is produced.

### Custom Handler Example

```python
def handle_crisis(value: str, fail_result):
    """Route crisis to human support."""
    # Log for review
    log_crisis_event(fail_result.metadata)

    # Use the recommended reply if available
    if fail_result.fix_value:
        return fail_result.fix_value

    # Or build your own response
    resources = fail_result.metadata.get("resources", {})
    if resources.get("primary"):
        return f"I want to make sure you're okay. Here's someone who can help: {resources['primary']['phone']}"
    return None

guard = Guard().use(CrisisScreen(
    include_recommended_reply=True,
    on_fail=handle_crisis
))
```

## Severity Levels

| Level | Description | Example |
|-------|-------------|---------|
| `mild` | Minor distress, no functional impairment | Vague expressions of sadness |
| `moderate` | Clear concern, not immediately dangerous | Passive suicidal ideation |
| `high` | Serious risk requiring urgent intervention | Active ideation with method |
| `critical` | Life-threatening, imminent harm | Intent + plan + timeline |

## Accuracy

See [nope.net/methodology](https://nope.net/methodology) for validation methodology, risk-framework grounding, and benchmark results.

## What NOPE Is Not

- **Not predictive:** Detects current signals, not future behavior
- **Not diagnostic:** Does not diagnose mental health conditions
- **Not therapeutic:** Does not provide treatment
- **Not a replacement** for human clinical judgment

## Local Development

```bash
git clone https://github.com/nope-net/guardrails-validator
cd guardrails-validator
pip install -e ".[dev]"
pytest tests/
```

## Links

- [NOPE Website](https://nope.net)
- [API Documentation](https://docs.nope.net)
- [Dashboard](https://dashboard.nope.net)
- [Guardrails Hub](https://hub.guardrailsai.com)

## License

Apache 2.0
