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
from guardrails.validator_base import FailResult, PassResult

from nope_crisis_screen.main import CrisisScreen

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
    result = validator._validate(
        "I want to kill myself tonight, I have the pills ready."
    )
    assert isinstance(result, FailResult)
    assert result.fix_value, "on_fail='fix' should yield a safe reply built from resources"
