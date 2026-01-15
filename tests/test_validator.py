"""Tests for the NOPE Crisis Screen validator."""

import os
from unittest.mock import patch

import pytest
import responses
from guardrails.validator_base import FailResult, PassResult

from nope_crisis_screen.main import CrisisScreen, ALL_RISK_TYPES, SEVERITY_ORDER, VALID_THRESHOLDS


class TestCrisisScreenInit:
    """Test validator initialization."""

    def test_requires_api_key(self):
        """Raises if no API key provided."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove NOPE_API_KEY if present
            os.environ.pop("NOPE_API_KEY", None)
            with pytest.raises(ValueError, match="NOPE API key required"):
                CrisisScreen()

    def test_accepts_api_key_param(self):
        """Accepts API key as parameter."""
        validator = CrisisScreen(api_key="test_key")
        assert validator.api_key == "test_key"

    def test_accepts_api_key_env(self):
        """Accepts API key from environment."""
        with patch.dict(os.environ, {"NOPE_API_KEY": "env_key"}):
            validator = CrisisScreen()
            assert validator.api_key == "env_key"

    def test_param_overrides_env(self):
        """Parameter API key overrides environment."""
        with patch.dict(os.environ, {"NOPE_API_KEY": "env_key"}):
            validator = CrisisScreen(api_key="param_key")
            assert validator.api_key == "param_key"

    def test_invalid_severity_threshold(self):
        """Raises on invalid severity threshold."""
        with pytest.raises(ValueError, match="Invalid severity_threshold"):
            CrisisScreen(api_key="test", severity_threshold="invalid")

    def test_valid_severity_thresholds(self):
        """Accepts all valid severity thresholds."""
        for severity in VALID_THRESHOLDS:
            validator = CrisisScreen(api_key="test", severity_threshold=severity)
            assert validator.severity_threshold == severity

    def test_rejects_none_severity_threshold(self):
        """Rejects 'none' as a severity threshold (not useful)."""
        with pytest.raises(ValueError, match="Invalid severity_threshold"):
            CrisisScreen(api_key="test", severity_threshold="none")

    def test_invalid_risk_types(self):
        """Raises on invalid risk types."""
        with pytest.raises(ValueError, match="Invalid risk types"):
            CrisisScreen(api_key="test", risk_types=["suicide", "invalid_type"])

    def test_valid_risk_types(self):
        """Accepts valid risk types subset."""
        validator = CrisisScreen(api_key="test", risk_types=["suicide", "self_harm"])
        assert validator.risk_types == ["suicide", "self_harm"]

    def test_defaults(self):
        """Validates default values."""
        validator = CrisisScreen(api_key="test")
        assert validator.severity_threshold == "moderate"
        assert validator.risk_types == ALL_RISK_TYPES
        assert validator.country == "US"
        assert validator.include_resources is True

    def test_fix_on_fail_auto_enables_recommended_reply(self):
        """on_fail='fix' automatically enables include_recommended_reply."""
        validator = CrisisScreen(api_key="test", on_fail="fix")
        assert validator.include_recommended_reply is True

    def test_fix_reask_auto_enables_recommended_reply(self):
        """on_fail='fix_reask' automatically enables include_recommended_reply."""
        validator = CrisisScreen(api_key="test", on_fail="fix_reask")
        assert validator.include_recommended_reply is True

    def test_include_recommended_reply_default_false(self):
        """include_recommended_reply defaults to False."""
        validator = CrisisScreen(api_key="test")
        assert validator.include_recommended_reply is False

    def test_include_recommended_reply_explicit(self):
        """include_recommended_reply can be set explicitly."""
        validator = CrisisScreen(api_key="test", include_recommended_reply=True)
        assert validator.include_recommended_reply is True


class TestSeverityThreshold:
    """Test severity threshold logic."""

    @pytest.fixture
    def validator(self):
        return CrisisScreen(api_key="test", severity_threshold="moderate")

    def test_severity_below_threshold(self, validator):
        """Severity below threshold returns False."""
        assert validator._severity_meets_threshold("none") is False
        assert validator._severity_meets_threshold("low") is False

    def test_severity_at_threshold(self, validator):
        """Severity at threshold returns True."""
        assert validator._severity_meets_threshold("moderate") is True

    def test_severity_above_threshold(self, validator):
        """Severity above threshold returns True."""
        assert validator._severity_meets_threshold("high") is True
        assert validator._severity_meets_threshold("critical") is True

    def test_invalid_severity(self, validator):
        """Invalid severity returns False."""
        assert validator._severity_meets_threshold("invalid") is False


class TestValidation:
    """Test the _validate method."""

    @pytest.fixture
    def validator(self):
        return CrisisScreen(api_key="test_key", severity_threshold="moderate")

    @responses.activate
    def test_pass_no_risks(self, validator):
        """Passes when no risks detected."""
        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={
                "risks": [],
                "show_resources": False,
                "rationale": "No concerning signals",
                "request_id": "screen_123",
                "timestamp": "2025-01-15T00:00:00Z",
            },
            status=200,
        )

        result = validator._validate("Hello, how are you?")

        assert isinstance(result, PassResult)
        assert result.metadata["request_id"] == "screen_123"
        assert result.metadata["risks_detected"] == 0

    @responses.activate
    def test_pass_risks_below_threshold(self, validator):
        """Passes when risks are below severity threshold."""
        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={
                "risks": [
                    {
                        "type": "suicide",
                        "severity": "low",  # Below "moderate" threshold
                        "imminence": "chronic",
                        "subject": "self",
                        "confidence": 0.6,
                    }
                ],
                "show_resources": False,
                "rationale": "Minor distress signals",
                "request_id": "screen_123",
                "timestamp": "2025-01-15T00:00:00Z",
            },
            status=200,
        )

        result = validator._validate("I feel a bit sad today")

        assert isinstance(result, PassResult)
        assert result.metadata["risks_detected"] == 1
        assert result.metadata["risks_filtered"] == 1

    @responses.activate
    def test_fail_risks_at_threshold(self, validator):
        """Fails when risks meet severity threshold."""
        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={
                "risks": [
                    {
                        "type": "suicide",
                        "severity": "moderate",
                        "imminence": "chronic",
                        "subject": "self",
                        "confidence": 0.8,
                    }
                ],
                "show_resources": True,
                "rationale": "Passive suicidal ideation",
                "resources": {
                    "primary": {
                        "name": "988 Suicide & Crisis Lifeline",
                        "phone": "988",
                    },
                    "secondary": [],
                },
                "request_id": "screen_123",
                "timestamp": "2025-01-15T00:00:00Z",
            },
            status=200,
        )

        result = validator._validate("I wish I wasn't here anymore")

        assert isinstance(result, FailResult)
        assert "suicide(moderate)" in result.error_message
        assert result.metadata["request_id"] == "screen_123"
        assert len(result.metadata["risks"]) == 1
        assert result.metadata["resources"]["primary"]["phone"] == "988"

    @responses.activate
    def test_filters_by_risk_type(self):
        """Only checks configured risk types."""
        validator = CrisisScreen(
            api_key="test", risk_types=["violence"]  # Only check violence
        )

        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={
                "risks": [
                    {
                        "type": "suicide",  # Not in our filter
                        "severity": "high",
                        "imminence": "acute",
                        "subject": "self",
                        "confidence": 0.9,
                    }
                ],
                "show_resources": True,
                "rationale": "Suicidal ideation",
                "request_id": "screen_123",
                "timestamp": "2025-01-15T00:00:00Z",
            },
            status=200,
        )

        result = validator._validate("...")

        # Should pass because suicide is not in our risk_types filter
        assert isinstance(result, PassResult)

    @responses.activate
    def test_uses_text_field(self, validator):
        """Sends text field when no messages in metadata."""
        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={"risks": [], "request_id": "123", "timestamp": "2025-01-15T00:00:00Z"},
            status=200,
        )

        validator._validate("Hello world")

        assert responses.calls[0].request.body
        import json

        body = json.loads(responses.calls[0].request.body)
        assert body["text"] == "Hello world"
        assert "messages" not in body

    @responses.activate
    def test_uses_messages_from_metadata(self, validator):
        """Sends messages when provided in metadata."""
        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={"risks": [], "request_id": "123", "timestamp": "2025-01-15T00:00:00Z"},
            status=200,
        )

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
        """Uses country from validator config."""
        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={"risks": [], "request_id": "123", "timestamp": "2025-01-15T00:00:00Z"},
            status=200,
        )

        validator._validate("Hello")

        import json

        body = json.loads(responses.calls[0].request.body)
        assert body["config"]["country"] == "US"

    @responses.activate
    def test_country_override_from_metadata(self, validator):
        """Metadata country overrides config."""
        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={"risks": [], "request_id": "123", "timestamp": "2025-01-15T00:00:00Z"},
            status=200,
        )

        validator._validate("Hello", metadata={"country": "GB"})

        import json

        body = json.loads(responses.calls[0].request.body)
        assert body["config"]["country"] == "GB"


class TestErrorHandling:
    """Test error handling behavior."""

    @pytest.fixture
    def validator(self):
        return CrisisScreen(api_key="test_key")

    def test_timeout_fails_open(self, validator):
        """Timeout results in PassResult (fail open)."""
        import requests as req_lib

        with patch.object(req_lib, "post") as mock_post:
            mock_post.side_effect = req_lib.exceptions.Timeout("Connection timeout")
            result = validator._validate("Hello")

        assert isinstance(result, PassResult)
        assert result.metadata.get("fail_open") is True
        assert "timeout" in result.metadata.get("error", "").lower()

    def test_401_raises_value_error(self, validator):
        """401 unauthorized raises ValueError."""
        import requests as req_lib

        mock_response = type("MockResponse", (), {
            "status_code": 401,
            "json": lambda self: {"error": "Invalid API key"},
            "raise_for_status": lambda self: None,
        })()

        def mock_post_with_401(*args, **kwargs):
            mock_response.raise_for_status = lambda: (_ for _ in ()).throw(
                req_lib.exceptions.HTTPError(response=mock_response)
            )
            return mock_response

        with patch.object(req_lib, "post", side_effect=mock_post_with_401):
            # HTTPError is raised by raise_for_status, need different approach
            pass

        # Simpler approach: mock the whole request flow
        with patch("requests.post") as mock_post:
            mock_resp = mock_post.return_value
            mock_resp.status_code = 401
            mock_resp.json.return_value = {"error": "Invalid API key"}
            error = req_lib.exceptions.HTTPError(response=mock_resp)
            mock_resp.raise_for_status.side_effect = error

            with pytest.raises(ValueError, match="NOPE API error"):
                validator._validate("Hello")

    def test_402_raises_value_error(self, validator):
        """402 insufficient balance raises ValueError."""
        import requests as req_lib

        with patch("requests.post") as mock_post:
            mock_resp = mock_post.return_value
            mock_resp.status_code = 402
            mock_resp.json.return_value = {"error": "Insufficient balance"}
            error = req_lib.exceptions.HTTPError(response=mock_resp)
            mock_resp.raise_for_status.side_effect = error

            with pytest.raises(ValueError, match="NOPE API error"):
                validator._validate("Hello")

    def test_500_fails_open(self, validator):
        """500 server error results in PassResult (fail open)."""
        import requests as req_lib

        with patch("requests.post") as mock_post:
            mock_resp = mock_post.return_value
            mock_resp.status_code = 500
            mock_resp.json.return_value = {"error": "Internal server error"}
            error = req_lib.exceptions.HTTPError(response=mock_resp)
            mock_resp.raise_for_status.side_effect = error

            result = validator._validate("Hello")

        assert isinstance(result, PassResult)
        assert result.metadata.get("fail_open") is True
        assert result.metadata.get("status_code") == 500

    @responses.activate
    def test_429_fails_open(self, validator):
        """429 rate limit results in PassResult (fail open)."""
        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={"error": "Rate limited"},
            status=429,
        )

        result = validator._validate("Hello")

        assert isinstance(result, PassResult)
        assert result.metadata.get("fail_open") is True

    def test_network_error_fails_open(self, validator):
        """Network errors result in PassResult (fail open)."""
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
        """Resources included in failure by default."""
        validator = CrisisScreen(api_key="test")

        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={
                "risks": [{"type": "suicide", "severity": "high", "imminence": "acute", "subject": "self", "confidence": 0.9}],
                "resources": {"primary": {"name": "988"}, "secondary": []},
                "rationale": "Crisis",
                "request_id": "123",
                "timestamp": "2025-01-15T00:00:00Z",
            },
            status=200,
        )

        result = validator._validate("...")
        assert "resources" in result.metadata

    @responses.activate
    def test_excludes_resources_when_disabled(self):
        """Resources excluded when include_resources=False."""
        validator = CrisisScreen(api_key="test", include_resources=False)

        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={
                "risks": [{"type": "suicide", "severity": "high", "imminence": "acute", "subject": "self", "confidence": 0.9}],
                "resources": {"primary": {"name": "988"}, "secondary": []},
                "rationale": "Crisis",
                "request_id": "123",
                "timestamp": "2025-01-15T00:00:00Z",
            },
            status=200,
        )

        result = validator._validate("...")
        assert "resources" not in result.metadata


class TestRecommendedReply:
    """Test include_recommended_reply and fix_value."""

    @responses.activate
    def test_recommended_reply_used_as_fix_value(self):
        """recommended_reply.content is returned as fix_value."""
        validator = CrisisScreen(api_key="test", include_recommended_reply=True)

        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={
                "risks": [{"type": "suicide", "severity": "high", "imminence": "acute", "subject": "self", "confidence": 0.9}],
                "resources": {"primary": {"name": "988"}, "secondary": []},
                "rationale": "Crisis",
                "request_id": "123",
                "timestamp": "2025-01-15T00:00:00Z",
                "recommended_reply": {
                    "content": "I hear you. Please reach out to 988.",
                    "source": "llm_generated"
                },
            },
            status=200,
        )

        result = validator._validate("...")

        assert isinstance(result, FailResult)
        assert result.fix_value == "I hear you. Please reach out to 988."
        assert result.metadata["recommended_reply"]["source"] == "llm_generated"

    @responses.activate
    def test_no_fix_value_without_recommended_reply(self):
        """fix_value is None when include_recommended_reply=False."""
        validator = CrisisScreen(api_key="test", include_recommended_reply=False)

        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={
                "risks": [{"type": "suicide", "severity": "high", "imminence": "acute", "subject": "self", "confidence": 0.9}],
                "rationale": "Crisis",
                "request_id": "123",
                "timestamp": "2025-01-15T00:00:00Z",
            },
            status=200,
        )

        result = validator._validate("...")

        assert isinstance(result, FailResult)
        assert result.fix_value is None

    @responses.activate
    def test_config_includes_recommended_reply_flag(self):
        """API request includes include_recommended_reply in config."""
        validator = CrisisScreen(api_key="test", include_recommended_reply=True)

        responses.add(
            responses.POST,
            "https://api.nope.net/v1/screen",
            json={
                "risks": [],
                "request_id": "123",
                "timestamp": "2025-01-15T00:00:00Z",
            },
            status=200,
        )

        validator._validate("Hello")

        import json
        body = json.loads(responses.calls[0].request.body)
        assert body["config"]["include_recommended_reply"] is True
