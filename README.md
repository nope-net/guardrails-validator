# NOPE Crisis Screen Validator

A [Guardrails AI](https://guardrailsai.com) validator for detecting mental health crises and safety risks in LLM inputs and outputs using [NOPE](https://nope.net).

- **Latency:** ~300-500ms (add ~500ms for `on_fail="fix"`)
- **Cost:** $0.001 per call ($1 free credit for new accounts)
- **Coverage:** 9 risk types, 222 countries

## Installation

```bash
pip install nope-crisis-screen
```

Or via Guardrails Hub (after publication):

```bash
guardrails hub install hub://nope/crisis_screen
```

## Requirements

- Python 3.9+
- A NOPE API key ([get one free](https://dashboard.nope.net))

## Safety Design

This validator **fails open** - if the NOPE API is unavailable, validation passes rather than blocking users. This prevents the safety layer from becoming a denial-of-service vector.

| Scenario | Behavior | Metadata |
|----------|----------|----------|
| Network error | Pass | `{"fail_open": true, "error": "..."}` |
| API timeout | Pass | `{"fail_open": true, "error": "NOPE API timeout"}` |
| Rate limited (429) | Pass | `{"fail_open": true}` |
| Auth error (401/402) | **Raise exception** | Configuration issue |

## Quick Start

```python
import os
from guardrails import Guard
from nope_crisis_screen import CrisisScreen

# Set your API key
os.environ["NOPE_API_KEY"] = "nope_live_xxx"

# Create a guard
guard = Guard().use(CrisisScreen(severity_threshold="moderate"))

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

## Regulatory Compliance

**California SB 243** (effective Jan 2026) requires AI chatbots to detect and respond to mental health crises. This validator helps you comply.

Also relevant for:
- **NY Article 47** - Mental health parity in digital services
- **UK Online Safety Act** - Duty of care for user safety
- **EU AI Act** - High-risk AI system requirements

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

When risks are detected, the validator returns localized crisis resources (hotlines, chat services) for 222 countries/territories.

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | `NOPE_API_KEY` env var | Your NOPE API key |
| `severity_threshold` | `str` | `"moderate"` | Minimum severity: `low`, `moderate`, `high`, `critical` |
| `risk_types` | `list[str]` | All 9 types | Which risk types to check |
| `country` | `str` | `"US"` | ISO country code for localized resources |
| `include_resources` | `bool` | `True` | Include crisis resources in failure metadata |
| `include_recommended_reply` | `bool` | `False` | Generate LLM-crafted safe response (adds ~500ms) |
| `on_fail` | `str \| Callable` | `None` | Guardrails on_fail action |

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
    severity_threshold="low",
))
```

### With Conversation Context

The API uses the last 6 messages for context, improving accuracy:

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

All standard Guardrails on_fail actions are supported:

| Action | Behavior | Use Case |
|--------|----------|----------|
| `exception` | Raise `ValidationError` | Hard stop, alert system |
| `noop` | Log but continue | Monitoring, analytics |
| `refrain` | Return `None` | Silent filtering |
| `fix` | Replace with safe response | Auto-respond to crisis with resources |
| `fix_reask` | Fix then reask if needed | Fallback chain |
| Custom function | Your handler | Route to human, show resources |

### Using `on_fail="fix"`

When you use `on_fail="fix"`, the validator automatically enables `include_recommended_reply` and returns an LLM-generated safe response:

```python
guard = Guard().use(CrisisScreen(on_fail="fix"))

result = guard.validate("I've been thinking about ending it all")
# result.validated_output contains a safe, supportive response with crisis resources
```

**Note:** This adds ~500ms latency for the recommended reply generation.

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
| `low` | Minor distress, no functional impairment | Vague expressions of sadness |
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
