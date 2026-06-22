"""Tests for the NOPE Crisis Screen validator.

These tests mock the /v1/evaluate wire shape. IMPORTANT: error-path tests use the
`responses` library (real requests.Response objects) rather than MagicMock, because
a requests.Response is FALSY for 4xx/5xx (Response.__bool__ == .ok) — a MagicMock is
truthy and would mask that. See test_401_raises_value_error.
"""

import os
from unittest.mock import patch

import pytest
import responses
from guardrails.validator_base import FailResult, PassResult

from nope_crisis_screen.main import (
    ALL_RISK_TYPES,
    SEVERITY_ORDER,
    VALID_THRESHOLDS,
    CrisisScreen,
)

EVAL_URL = "https://api.nope.net/v1/evaluate"


def _eval_response(risks=None, resources=None, rationale="", request_id="eval_123",
                   speaker_severity="none", speaker_imminence="not_applicable"):
    """Build a minimal /v1/evaluate response body."""
    body = {
        "risks": risks or [],
        "rationale": rationale,
        "speaker_severity": speaker_severity,
        "speaker_imminence": speaker_imminence,
        "show_resources": bool(resources),
        "request_id": request_id,
        "timestamp": "2026-01-15T00:00:00Z",
        "metadata": {"api_version": "v1", "input_format": "text_blob"},
    }
    if resources is not None:
        body["resources"] = resources
    return body


class TestCrisisScreenInit:
    """Test validator initialization."""

    def test_requires_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("NOPE_API_KEY", None)
            with pytest.raises(ValueError, match="NOPE API key required"):
                CrisisScreen()

    def test_accepts_api_key_param(self):
        validator = CrisisScreen(api_key="test_key")
        assert validator.api_key == "test_key"

    def test_accepts_api_key_env(self):
        with patch.dict(os.environ, {"NOPE_API_KEY": "env_key"}):
            validator = CrisisScreen()
            assert validator.api_key == "env_key"

    def test_param_overrides_env(self):
        with patch.dict(os.environ, {"NOPE_API_KEY": "env_key"}):
            validator = CrisisScreen(api_key="param_key")
            assert validator.api_key == "param_key"

    def test_invalid_severity_threshold(self):
        with pytest.raises(ValueError, match="Invalid severity_threshold"):
            CrisisScreen(api_key="test", severity_threshold="invalid")

    def test_valid_severity_thresholds(self):
        for severity in VALID_THRESHOLDS:
            validator = CrisisScreen(api_key="test", severity_threshold=severity)
            assert validator.severity_threshold == severity

    def test_rejects_none_severity_threshold(self):
        with pytest.raises(ValueError, match="Invalid severity_threshold"):
            CrisisScreen(api_key="test", severity_threshold="none")

    def test_low_threshold_is_deprecated_alias_for_mild(self):
        """'low' (never an API value) maps to 'mild' with a DeprecationWarning."""
        with pytest.warns(DeprecationWarning):
            validator = CrisisScreen(api_key="test", severity_threshold="low")
        assert validator.severity_threshold == "mild"

    def test_invalid_risk_types(self):
        with pytest.raises(ValueError, match="Invalid risk types"):
            CrisisScreen(api_key="test", risk_types=["suicide", "invalid_type"])

    def test_valid_risk_types(self):
        validator = CrisisScreen(api_key="test", risk_types=["suicide", "self_harm"])
        assert validator.risk_types == ["suicide", "self_harm"]

    def test_defaults(self):
        validator = CrisisScreen(api_key="test")
        assert validator.severity_threshold == "moderate"
        assert validator.risk_types == ALL_RISK_TYPES
        assert validator.country == "US"
        assert validator.include_resources is True

    def test_severity_order_matches_api(self):
        """Regression: the ladder must use the API's 'mild', never 'low'."""
        assert SEVERITY_ORDER == ["none", "mild", "moderate", "high", "critical"]
        assert "low" not in SEVERITY_ORDER

    def test_fix_on_fail_auto_enables_recommended_reply(self):
        validator = CrisisScreen(api_key="test", on_fail="fix")
        assert validator.include_recommended_reply is True

    def test_fix_reask_auto_enables_recommended_reply(self):
        validator = CrisisScreen(api_key="test", on_fail="fix_reask")
        assert validator.include_recommended_reply is True

    def test_include_recommended_reply_default_false(self):
        validator = CrisisScreen(api_key="test")
        assert validator.include_recommended_reply is False


class TestSeverityThreshold:
    """Test severity threshold logic."""

    @pytest.fixture
    def validator(self):
        return CrisisScreen(api_key="test", severity_threshold="moderate")

    def test_severity_below_threshold(self, validator):
        assert validator._severity_meets_threshold("none") is False
        assert validator._severity_meets_threshold("mild") is False

    def test_severity_at_threshold(self, validator):
        assert validator._severity_meets_threshold("moderate") is True

    def test_severity_above_threshold(self, validator):
        assert validator._severity_meets_threshold("high") is True
        assert validator._severity_meets_threshold("critical") is True

    def test_invalid_severity(self, validator):
        assert validator._severity_meets_threshold("invalid") is False
        # "low" is not a real API value and must not satisfy any threshold.
        assert validator._severity_meets_threshold("low") is False

    def test_mild_threshold_catches_mild(self):
        validator = CrisisScreen(api_key="test", severity_threshold="mild")
        assert validator._severity_meets_threshold("mild") is True
        assert validator._severity_meets_threshold("none") is False


class TestValidation:
    """Test the _validate method against the /v1/evaluate wire."""

    @pytest.fixture
    def validator(self):
        return CrisisScreen(api_key="test_key", severity_threshold="moderate")

    @responses.activate
    def test_pass_no_risks(self, validator):
        responses.add(responses.POST, EVAL_URL,
                      json=_eval_response(risks=[], rationale="No concerning signals"),
                      status=200)
        result = validator._validate("Hello, how are you?")
        assert isinstance(result, PassResult)
        assert result.metadata["request_id"] == "eval_123"
        assert result.metadata["risks_detected"] == 0

    @responses.activate
    def test_pass_risks_below_threshold(self, validator):
        responses.add(responses.POST, EVAL_URL, json=_eval_response(
            risks=[{"type": "suicide", "severity": "mild", "imminence": "chronic",
                    "subject": "self"}],
            rationale="Minor distress signals",
            speaker_severity="mild",
        ), status=200)
        result = validator._validate("I feel a bit sad today")
        assert isinstance(result, PassResult)
        assert result.metadata["risks_detected"] == 1
        assert result.metadata["risks_filtered"] == 1

    @responses.activate
    def test_fail_risks_at_threshold(self, validator):
        responses.add(responses.POST, EVAL_URL, json=_eval_response(
            risks=[{"type": "suicide", "severity": "moderate", "imminence": "chronic",
                    "subject": "self"}],
            rationale="Passive suicidal ideation",
            resources={"primary": {"name": "988 Suicide & Crisis Lifeline",
                                   "phone": "988"}, "secondary": []},
            speaker_severity="moderate",
        ), status=200)
        result = validator._validate("I wish I wasn't here anymore")
        assert isinstance(result, FailResult)
        assert "suicide(moderate)" in result.error_message
        assert result.metadata["request_id"] == "eval_123"
        assert len(result.metadata["risks"]) == 1
        assert result.metadata["resources"]["primary"]["phone"] == "988"
        assert result.metadata["speaker_severity"] == "moderate"

    @responses.activate
    def test_fail_on_critical(self, validator):
        responses.add(responses.POST, EVAL_URL, json=_eval_response(
            risks=[{"type": "suicide", "severity": "critical", "imminence": "emergency",
                    "subject": "self"}],
            rationale="Active intent with plan and timeline",
            resources={"primary": {"name": "988", "phone": "988"}, "secondary": []},
            speaker_severity="critical",
        ), status=200)
        result = validator._validate("I want to kill myself tonight, pills ready")
        assert isinstance(result, FailResult)
        assert "suicide(critical)" in result.error_message

    @responses.activate
    def test_filters_by_risk_type(self):
        validator = CrisisScreen(api_key="test", risk_types=["violence"])
        responses.add(responses.POST, EVAL_URL, json=_eval_response(
            risks=[{"type": "suicide", "severity": "high", "imminence": "urgent",
                    "subject": "self"}],
            rationale="Suicidal ideation",
        ), status=200)
        result = validator._validate("...")
        # Passes because suicide is not in the configured risk_types filter.
        assert isinstance(result, PassResult)

    @responses.activate
    def test_uses_text_field(self, validator):
        responses.add(responses.POST, EVAL_URL, json=_eval_response(), status=200)
        validator._validate("Hello world")
        import json
        body = json.loads(responses.calls[0].request.body)
        assert body["text"] == "Hello world"
        assert "messages" not in body

    @responses.activate
    def test_uses_messages_from_metadata(self, validator):
        responses.add(responses.POST, EVAL_URL, json=_eval_response(), status=200)
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        validator._validate("ignored", metadata={"messages": messages})
        import json
        body = json.loads(responses.calls[0].request.body)
        assert body["messages"] == messages
        assert "text" not in body

    @responses.activate
    def test_country_from_config(self, validator):
        responses.add(responses.POST, EVAL_URL, json=_eval_response(), status=200)
        validator._validate("Hello")
        import json
        body = json.loads(responses.calls[0].request.body)
        assert body["config"]["country"] == "US"
        # /v1/evaluate has no include_recommended_reply — never send it.
        assert "include_recommended_reply" not in body["config"]
        assert body["config"]["include_resources"] is True

    @responses.activate
    def test_country_override_from_metadata(self, validator):
        responses.add(responses.POST, EVAL_URL, json=_eval_response(), status=200)
        validator._validate("Hello", metadata={"country": "GB"})
        import json
        body = json.loads(responses.calls[0].request.body)
        assert body["config"]["country"] == "GB"


class TestErrorHandling:
    """Test error handling. Uses real Response objects via `responses`."""

    @pytest.fixture
    def validator(self):
        return CrisisScreen(api_key="test_key")

    def test_timeout_fails_open(self, validator):
        import requests as req_lib
        with patch.object(req_lib, "post") as mock_post:
            mock_post.side_effect = req_lib.exceptions.Timeout("Connection timeout")
            result = validator._validate("Hello")
        assert isinstance(result, PassResult)
        assert result.metadata.get("fail_open") is True
        assert "timeout" in result.metadata.get("error", "").lower()

    @responses.activate
    def test_401_raises_value_error(self, validator):
        """Regression: a real 401 Response is FALSY; must still raise (not fail open)."""
        responses.add(responses.POST, EVAL_URL, json={"error": "Invalid API key"},
                      status=401)
        with pytest.raises(ValueError, match="NOPE API error"):
            validator._validate("Hello")

    @responses.activate
    def test_402_raises_value_error(self, validator):
        responses.add(responses.POST, EVAL_URL, json={"error": "Insufficient balance"},
                      status=402)
        with pytest.raises(ValueError, match="NOPE API error"):
            validator._validate("Hello")

    @responses.activate
    def test_404_raises_value_error(self, validator):
        """Misconfiguration (wrong endpoint) must be LOUD, not a silent fail-open.

        This is the exact failure mode of the previous version, which pointed at the
        removed /v1/screen and silently passed everything.
        """
        responses.add(responses.POST, EVAL_URL, json={"error": "Not found"}, status=404)
        with pytest.raises(ValueError, match="client error"):
            validator._validate("Hello")

    @responses.activate
    def test_400_raises_value_error(self, validator):
        responses.add(responses.POST, EVAL_URL, json={"error": "Bad request"}, status=400)
        with pytest.raises(ValueError, match="client error"):
            validator._validate("Hello")

    @responses.activate
    def test_500_fails_open(self, validator):
        responses.add(responses.POST, EVAL_URL, json={"error": "Internal"}, status=500)
        result = validator._validate("Hello")
        assert isinstance(result, PassResult)
        assert result.metadata.get("fail_open") is True
        assert result.metadata.get("status_code") == 500

    @responses.activate
    def test_429_fails_open(self, validator):
        responses.add(responses.POST, EVAL_URL, json={"error": "Rate limited"}, status=429)
        result = validator._validate("Hello")
        assert isinstance(result, PassResult)
        assert result.metadata.get("fail_open") is True
        assert result.metadata.get("status_code") == 429

    def test_network_error_fails_open(self, validator):
        import requests as req_lib
        with patch.object(req_lib, "post") as mock_post:
            mock_post.side_effect = req_lib.exceptions.ConnectionError("Network error")
            result = validator._validate("Hello")
        assert isinstance(result, PassResult)
        assert result.metadata.get("fail_open") is True


class TestResourcesInclusion:
    """Test include_resources configuration."""

    @responses.activate
    def test_includes_resources_by_default(self):
        validator = CrisisScreen(api_key="test")
        responses.add(responses.POST, EVAL_URL, json=_eval_response(
            risks=[{"type": "suicide", "severity": "high", "imminence": "urgent",
                    "subject": "self"}],
            resources={"primary": {"name": "988", "phone": "988"}, "secondary": []},
            rationale="Crisis",
        ), status=200)
        result = validator._validate("...")
        assert "resources" in result.metadata

    @responses.activate
    def test_excludes_resources_when_disabled(self):
        validator = CrisisScreen(api_key="test", include_resources=False)
        responses.add(responses.POST, EVAL_URL, json=_eval_response(
            risks=[{"type": "suicide", "severity": "high", "imminence": "urgent",
                    "subject": "self"}],
            resources={"primary": {"name": "988", "phone": "988"}, "secondary": []},
            rationale="Crisis",
        ), status=200)
        result = validator._validate("...")
        assert "resources" not in result.metadata

    @responses.activate
    def test_include_resources_flag_forwarded_to_api(self):
        validator = CrisisScreen(api_key="test", include_resources=False)
        responses.add(responses.POST, EVAL_URL, json=_eval_response(), status=200)
        validator._validate("...")
        import json
        body = json.loads(responses.calls[0].request.body)
        assert body["config"]["include_resources"] is False


class TestRecommendedReply:
    """Test the deterministic safe-reply fix_value."""

    @responses.activate
    def test_safe_reply_built_from_resource(self):
        validator = CrisisScreen(api_key="test", include_recommended_reply=True)
        responses.add(responses.POST, EVAL_URL, json=_eval_response(
            risks=[{"type": "suicide", "severity": "high", "imminence": "urgent",
                    "subject": "self"}],
            resources={"primary": {"name": "988 Suicide & Crisis Lifeline",
                                   "phone": "988"}, "secondary": []},
            rationale="Crisis",
        ), status=200)
        result = validator._validate("...")
        assert isinstance(result, FailResult)
        assert result.fix_value is not None
        assert "988" in result.fix_value
        assert result.metadata["recommended_reply"]["source"] == "resource_template"

    @responses.activate
    def test_no_fix_value_without_recommended_reply(self):
        validator = CrisisScreen(api_key="test", include_recommended_reply=False)
        responses.add(responses.POST, EVAL_URL, json=_eval_response(
            risks=[{"type": "suicide", "severity": "high", "imminence": "urgent",
                    "subject": "self"}],
            rationale="Crisis",
        ), status=200)
        result = validator._validate("...")
        assert isinstance(result, FailResult)
        assert result.fix_value is None

    @responses.activate
    def test_no_fix_value_when_no_resources(self):
        """If the API returned no resources, there's nothing to build a reply from."""
        validator = CrisisScreen(api_key="test", include_recommended_reply=True)
        responses.add(responses.POST, EVAL_URL, json=_eval_response(
            risks=[{"type": "suicide", "severity": "high", "imminence": "urgent",
                    "subject": "self"}],
            rationale="Crisis",
        ), status=200)
        result = validator._validate("...")
        assert isinstance(result, FailResult)
        assert result.fix_value is None
