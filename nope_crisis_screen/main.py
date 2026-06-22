"""
NOPE Crisis Screen Validator for Guardrails AI

Detects mental health crises and safety risks in text using NOPE's
/v1/evaluate API (Edge-backed). Covers 9 risk types based on clinical
frameworks (C-SSRS for suicide, HCR-20 for violence, DASH for abuse).

Risk types: suicide, self_harm, self_neglect, violence, abuse,
sexual_violence, neglect, exploitation, stalking

For more information, see:
- NOPE API docs: https://docs.nope.net
- Guardrails Hub: https://hub.guardrailsai.com
"""

import os
import warnings
from typing import Any, Callable, Dict, List, Optional, Union

import requests
from guardrails.validator_base import (
    FailResult,
    PassResult,
    ValidationResult,
    Validator,
    register_validator,
)

# Severity ladder, low -> high. Must match the NOPE API's Severity enum exactly.
# NOTE: the API uses "mild" (NOT "low"). "none" is included for ordering only.
SEVERITY_ORDER = ["none", "mild", "moderate", "high", "critical"]

# Valid thresholds for configuration (excludes "none", which is not a useful threshold).
VALID_THRESHOLDS = ["mild", "moderate", "high", "critical"]

# Back-compat: earlier docs advertised "low", which the API never emitted. Treat as "mild".
_THRESHOLD_ALIASES = {"low": "mild"}

ALL_RISK_TYPES = [
    "suicide",
    "self_harm",
    "self_neglect",
    "violence",
    "abuse",
    "sexual_violence",
    "neglect",
    "exploitation",
    "stalking",
]


@register_validator(name="nope/crisis_screen", data_type="string")
class CrisisScreen(Validator):
    """
    Detects mental health crises and safety risks in text using NOPE's
    /v1/evaluate API.

    Covers 9 risk types:
    - suicide: Self-directed lethal intent (C-SSRS based)
    - self_harm: Non-suicidal self-injury
    - self_neglect: Self-care failure, eating disorders, substance crisis
    - violence: Risk of harm to others
    - abuse: Physical, emotional, sexual, financial abuse
    - sexual_violence: Rape, sexual assault, coercion
    - neglect: Failure to care for dependents
    - exploitation: Trafficking, grooming, sextortion
    - stalking: Persistent unwanted contact, surveillance

    Args:
        api_key: NOPE API key. Falls back to NOPE_API_KEY env var.
        severity_threshold: Minimum severity to trigger failure.
            Options: "mild", "moderate", "high", "critical"
            Default: "moderate"
        risk_types: List of risk types to check. Default: all 9 types.
        country: ISO country code for localized crisis resources.
            Default: "US"
        include_resources: Include crisis hotline info in failure metadata.
            Default: True
        include_recommended_reply: Attach a deterministic, supportive safe-reply
            (built from the matched crisis resource) as the FailResult fix_value,
            enabling on_fail="fix". Adds no latency and no extra API cost.
            Auto-enabled when on_fail="fix" or "fix_reask".
            Default: False
        on_fail: Guardrails on_fail action. Default: None (uses Guard default)

    Example:
        >>> from guardrails import Guard
        >>> from nope_crisis_screen import CrisisScreen
        >>>
        >>> guard = Guard().use(CrisisScreen(severity_threshold="moderate"))
        >>> guard.validate("I've been feeling hopeless lately")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        severity_threshold: str = "moderate",
        risk_types: Optional[List[str]] = None,
        country: str = "US",
        include_resources: bool = True,
        include_recommended_reply: bool = False,
        on_fail: Optional[Union[str, Callable]] = None,
    ):
        # Auto-enable the safe-reply fix_value when the user wants a fix action.
        if isinstance(on_fail, str) and on_fail.lower() in ("fix", "fix_reask"):
            include_recommended_reply = True

        # Back-compat: map deprecated "low" -> "mild".
        if severity_threshold in _THRESHOLD_ALIASES:
            warnings.warn(
                f"severity_threshold={severity_threshold!r} is deprecated; "
                f"use {_THRESHOLD_ALIASES[severity_threshold]!r}. The NOPE API "
                f"severity scale is {VALID_THRESHOLDS}.",
                DeprecationWarning,
                stacklevel=2,
            )
            severity_threshold = _THRESHOLD_ALIASES[severity_threshold]

        super().__init__(
            on_fail=on_fail,
            api_key=api_key,
            severity_threshold=severity_threshold,
            risk_types=risk_types,
            country=country,
            include_resources=include_resources,
            include_recommended_reply=include_recommended_reply,
        )
        self.api_key = api_key or os.getenv("NOPE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "NOPE API key required. Set NOPE_API_KEY environment variable "
                "or pass api_key parameter. Get your key at https://dashboard.nope.net"
            )

        if severity_threshold not in VALID_THRESHOLDS:
            raise ValueError(
                f"Invalid severity_threshold: {severity_threshold}. "
                f"Must be one of: {VALID_THRESHOLDS}"
            )
        self.severity_threshold = severity_threshold

        # Validate risk types
        if risk_types is not None:
            invalid_types = set(risk_types) - set(ALL_RISK_TYPES)
            if invalid_types:
                raise ValueError(
                    f"Invalid risk types: {invalid_types}. "
                    f"Must be from: {ALL_RISK_TYPES}"
                )
        self.risk_types = risk_types or ALL_RISK_TYPES

        self.country = country
        self.include_resources = include_resources
        self.include_recommended_reply = include_recommended_reply
        self._base_url = os.getenv("NOPE_API_URL", "https://api.nope.net")

    def _severity_meets_threshold(self, severity: str) -> bool:
        """Check if severity meets or exceeds threshold."""
        if severity not in SEVERITY_ORDER:
            return False
        return SEVERITY_ORDER.index(severity) >= SEVERITY_ORDER.index(
            self.severity_threshold
        )

    @staticmethod
    def _build_safe_reply(resources: Optional[Dict[str, Any]]) -> Optional[str]:
        """
        Build a deterministic, supportive reply from the primary crisis resource.

        Intentionally NOT LLM-generated: a fixed, resource-grounded message has no
        hallucination risk, adds no latency, and costs nothing. Returns None when
        no usable resource is available.
        """
        if not resources:
            return None
        primary = resources.get("primary") or {}
        name = primary.get("name")
        if not name:
            return None

        # Prefer the most actionable contact detail available.
        contact = None
        if primary.get("phone"):
            contact = f"call {primary['phone']}"
        elif primary.get("text_instructions"):
            contact = primary["text_instructions"]
        elif primary.get("chat_url"):
            contact = f"chat at {primary['chat_url']}"
        elif primary.get("website_url"):
            contact = f"visit {primary['website_url']}"

        reach_out = f"reaching out to {name}"
        if contact:
            reach_out += f" ({contact})"

        return (
            "It sounds like you're going through something really difficult, and "
            "you don't have to face it alone. Please consider "
            f"{reach_out}. Support is available right now."
        )

    def _validate(
        self, value: str, metadata: Optional[Dict[str, Any]] = None
    ) -> ValidationResult:
        """
        Validate text for crisis signals.

        Args:
            value: Text to screen (user message or AI response)
            metadata: Optional dict with:
                - messages: List of {"role": "user"|"assistant", "content": str}
                           for conversation context (improves accuracy)
                - country: Override default country code for this call

        Returns:
            PassResult if no concerning risks detected at or above threshold.
            FailResult if risks detected, with crisis resources in metadata.
        """
        metadata = metadata or {}

        # Build request payload for /v1/evaluate.
        config: Dict[str, Any] = {
            "country": metadata.get("country", self.country),
            # Only ask the API to compute resources if we'll surface them.
            "include_resources": self.include_resources,
        }
        payload: Dict[str, Any] = {"config": config}

        # Support conversation history via metadata.
        if "messages" in metadata:
            payload["messages"] = metadata["messages"]
        else:
            payload["text"] = value

        # Call NOPE API.
        try:
            response = requests.post(
                f"{self._base_url}/v1/evaluate",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.Timeout:
            # Transient: fail open so the safety layer never becomes a DoS vector.
            return PassResult(
                metadata={"error": "NOPE API timeout", "fail_open": True}
            )
        except requests.exceptions.HTTPError as e:
            # IMPORTANT: a requests.Response is FALSY for 4xx/5xx (Response.__bool__
            # returns .ok), so never gate on `if e.response` — check `is not None`.
            resp = getattr(e, "response", None)
            status_code = resp.status_code if resp is not None else None
            error_body = None
            if resp is not None:
                try:
                    error_body = resp.json()
                except Exception:
                    pass

            # 401 = invalid key, 402 = insufficient balance: developer must fix. Be loud.
            if status_code in (401, 402):
                raise ValueError(
                    f"NOPE API error ({status_code}): {error_body or str(e)}. "
                    "Check your API key and account balance at https://dashboard.nope.net"
                )

            # 429 rate limit: transient, fail open.
            if status_code == 429:
                return PassResult(
                    metadata={"error": str(e), "status_code": 429, "fail_open": True}
                )

            # Other 4xx (e.g. 400 bad request, 404 misconfigured URL): developer error.
            # Fail LOUD rather than silently passing — silent fail-open on a 4xx is
            # exactly how a misconfigured safety layer goes unnoticed.
            if status_code is not None and 400 <= status_code < 500:
                raise ValueError(
                    f"NOPE API client error ({status_code}): {error_body or str(e)}. "
                    "This indicates a misconfiguration (check NOPE_API_URL and request "
                    "shape), not a transient outage."
                )

            # 5xx and anything else: transient server-side, fail open.
            return PassResult(
                metadata={
                    "error": str(e),
                    "status_code": status_code,
                    "fail_open": True,
                }
            )
        except requests.exceptions.RequestException as e:
            # Network errors: transient, fail open.
            return PassResult(metadata={"error": str(e), "fail_open": True})

        # Filter risks by configured types and severity threshold.
        all_risks = result.get("risks", [])
        concerning_risks = [
            risk
            for risk in all_risks
            if risk.get("type") in self.risk_types
            and self._severity_meets_threshold(risk.get("severity", "none"))
        ]

        if not concerning_risks:
            return PassResult(
                metadata={
                    "request_id": result.get("request_id"),
                    "risks_detected": len(all_risks),
                    "risks_filtered": len(all_risks) - len(concerning_risks),
                }
            )

        # Build failure response.
        resources = result.get("resources")
        failure_metadata: Dict[str, Any] = {
            "request_id": result.get("request_id"),
            "risks": concerning_risks,
            "rationale": result.get("rationale"),
            "speaker_severity": result.get("speaker_severity"),
            "speaker_imminence": result.get("speaker_imminence"),
        }

        if self.include_resources and resources:
            failure_metadata["resources"] = resources

        # Synthesize a deterministic safe reply for fix_value (enables on_fail="fix").
        fix_value = None
        if self.include_recommended_reply:
            fix_value = self._build_safe_reply(resources)
            if fix_value:
                failure_metadata["recommended_reply"] = {
                    "content": fix_value,
                    "source": "resource_template",
                }

        # Format error message with risk summary.
        risk_summary = ", ".join(
            f"{r.get('type')}({r.get('severity')})" for r in concerning_risks
        )

        return FailResult(
            error_message=(
                f"Crisis signals detected: {risk_summary}. "
                f"{result.get('rationale', '')}"
            ),
            fix_value=fix_value,
            metadata=failure_metadata,
        )
