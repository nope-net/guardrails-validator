"""Live smoke test against the real NOPE API.

This is the test the previous version lacked: it hits production /v1/evaluate and
asserts real behaviour. The mocked unit tests can pass against a *fictional* contract
(as the old suite did against the removed /v1/screen) — only a live call proves the
validator is actually wired to a working endpoint.

Runs only when NOPE_API_KEY is set; otherwise skipped. Each call costs ~$0.003.

    NOPE_API_KEY=nope_... pytest tests/test_live_smoke.py -v
"""

import os

import pytest
from guardrails import Guard
from guardrails.errors import ValidationError
from guardrails.validator_base import FailResult, PassResult

from nope_crisis_screen.main import CrisisScreen

BENIGN = "What's a good recipe for banana bread?"
CRISIS = "I want to kill myself tonight, I have the pills ready."

pytestmark = pytest.mark.skipif(
    not os.getenv("NOPE_API_KEY"),
    reason="NOPE_API_KEY not set — skipping live API smoke test",
)


@pytest.fixture(scope="module")
def validator():
    return CrisisScreen(severity_threshold="moderate")


def test_benign_passes(validator):
    result = validator._validate("What's a good recipe for banana bread?")
    assert isinstance(result, PassResult), (
        f"benign text should pass, got {type(result).__name__}: "
        f"{getattr(result, 'metadata', None)}"
    )
    # Must be a real assessment, not a silent fail-open.
    assert result.metadata.get("fail_open") is not True, (
        "benign passed only because the API call failed open — endpoint is misconfigured"
    )
    assert result.metadata.get("request_id"), "expected a real request_id from the API"


def test_explicit_suicide_crisis_fails(validator):
    result = validator._validate(
        "I want to kill myself tonight, I have the pills ready."
    )
    assert isinstance(result, FailResult), (
        f"explicit suicidal intent must FAIL the guardrail, got "
        f"{type(result).__name__}: {getattr(result, 'metadata', None)}"
    )
    risk_types = {r.get("type") for r in result.metadata.get("risks", [])}
    assert "suicide" in risk_types, f"expected a suicide risk, got {risk_types}"
    assert result.metadata.get("request_id"), "expected a real request_id from the API"


def test_fix_action_produces_safe_reply():
    validator = CrisisScreen(on_fail="fix")  # auto-enables the safe reply
    result = validator._validate(CRISIS)
    assert isinstance(result, FailResult)
    assert result.fix_value, "on_fail='fix' should yield a safe reply built from resources"


# ---------------------------------------------------------------------------
# Full end-to-end through the real Guardrails framework (Guard().validate),
# not the internal _validate() hook. This is the path a real user invokes.
# ---------------------------------------------------------------------------


def test_e2e_guard_benign_passes():
    guard = Guard().use(CrisisScreen(severity_threshold="moderate", on_fail="noop"))
    outcome = guard.validate(BENIGN)
    assert outcome.validation_passed is True
    assert outcome.validated_output == BENIGN


def test_e2e_guard_noop_reports_failure_without_raising():
    guard = Guard().use(CrisisScreen(severity_threshold="moderate", on_fail="noop"))
    outcome = guard.validate(CRISIS)
    assert outcome.validation_passed is False
    assert outcome.validation_summaries, "expected a validation summary on failure"


def test_e2e_guard_exception_raises_on_crisis():
    guard = Guard().use(CrisisScreen(on_fail="exception"))
    with pytest.raises(ValidationError, match="Crisis signals detected"):
        guard.validate(CRISIS)


def test_e2e_guard_fix_substitutes_safe_reply():
    guard = Guard().use(CrisisScreen(on_fail="fix"))
    outcome = guard.validate(CRISIS)
    # fix replaces the offending text with the deterministic safe reply.
    assert outcome.validation_passed is True
    assert outcome.validated_output != CRISIS
    assert "reaching out to" in outcome.validated_output
