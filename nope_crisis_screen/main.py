"""
NOPE Crisis Screen Validator for Guardrails AI

Detects mental health crises and safety risks in text using NOPE's
/v1/screen API. Covers 9 risk types based on clinical frameworks
(C-SSRS for suicide, HCR-20 for violence, DASH for abuse).

Risk types: suicide, self_harm, self_neglect, violence, abuse,
sexual_violence, neglect, exploitation, stalking

For more information, see:
- NOPE API docs: https://docs.nope.net
- Guardrails Hub: https://hub.guardrailsai.com
"""

import os
from typing import Any, Callable, Dict, List, Optional, Union

import requests
from guardrails.validator_base import (
    FailResult,
    PassResult,
    ValidationResult,
    Validator,
    register_validator,
)

# All severity levels (for comparison logic)
SEVERITY_ORDER = ["none", "low", "moderate", "high", "critical"]

# Valid thresholds for configuration (excludes "none" which is not a useful threshold)
VALID_THRESHOLDS = ["low", "moderate", "high", "critical"]

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
    /v1/screen API.

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
            Options: "low", "moderate", "high", "critical"
            Default: "moderate"
        risk_types: List of risk types to check. Default: all 9 types.
        country: ISO country code for localized crisis resources.
            Default: "US"
        include_resources: Include crisis hotline info in failure metadata.
            Default: True
        include_recommended_reply: Generate an LLM-crafted safe response for
            crisis situations. Enables on_fail="fix" action. Adds ~500ms latency.
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
        # Auto-enable recommended_reply if user wants fix action
        if isinstance(on_fail, str) and on_fail.lower() in ("fix", "fix_reask"):
            include_recommended_reply = True

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

    def _validate(
        self, value: str, metadata: Optional[Dict[str, Any]] = None
    ) -> ValidationResult:
        """
        Validate text for crisis signals.

        Args:
            value: Text to screen (user message or AI response)
            metadata: Optional dict with:
                - messages: List of {"role": "user"|"assistant", "content": str}
                           for conversation context (API uses last 6 messages)
                - country: Override default country code for this call

        Returns:
            PassResult if no concerning risks detected at or above threshold.
            FailResult if risks detected, with crisis resources in metadata.
        """
        metadata = metadata or {}

        # Build request payload
        config: Dict[str, Any] = {"country": metadata.get("country", self.country)}
        if self.include_recommended_reply:
            config["include_recommended_reply"] = True
        payload: Dict[str, Any] = {"config": config}

        # Support conversation history via metadata
        if "messages" in metadata:
            payload["messages"] = metadata["messages"]
        else:
            payload["text"] = value

        # Call NOPE API
        try:
            # Longer timeout when generating recommended reply (adds LLM call)
            timeout = 30 if self.include_recommended_reply else 10

            response = requests.post(
                f"{self._base_url}/v1/screen",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.Timeout:
            # Fail open on timeout - don't block users
            return PassResult(
                metadata={"error": "NOPE API timeout", "fail_open": True}
            )
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else None
            error_body = None
            try:
                error_body = e.response.json() if e.response else None
            except Exception:
                pass

            # 402 = insufficient balance, 401 = invalid key
            if status_code in (401, 402):
                raise ValueError(
                    f"NOPE API error ({status_code}): {error_body or str(e)}. "
                    "Check your API key and account balance at https://dashboard.nope.net"
                )

            # Other errors: fail open
            return PassResult(
                metadata={
                    "error": str(e),
                    "status_code": status_code,
                    "fail_open": True,
                }
            )
        except requests.exceptions.RequestException as e:
            # Network errors: fail open
            return PassResult(metadata={"error": str(e), "fail_open": True})

        # Filter risks by configured types and severity threshold
        all_risks = result.get("risks", [])
        concerning_risks = [
            risk
            for risk in all_risks
            if risk["type"] in self.risk_types
            and self._severity_meets_threshold(risk["severity"])
        ]

        if not concerning_risks:
            return PassResult(
                metadata={
                    "request_id": result.get("request_id"),
                    "risks_detected": len(all_risks),
                    "risks_filtered": len(all_risks) - len(concerning_risks),
                }
            )

        # Build failure response
        failure_metadata: Dict[str, Any] = {
            "request_id": result.get("request_id"),
            "risks": concerning_risks,
            "rationale": result.get("rationale"),
        }

        if self.include_resources and result.get("resources"):
            failure_metadata["resources"] = result["resources"]

        # Extract recommended reply for fix_value (enables on_fail="fix")
        fix_value = None
        recommended_reply = result.get("recommended_reply")
        if recommended_reply and recommended_reply.get("content"):
            fix_value = recommended_reply["content"]
            failure_metadata["recommended_reply"] = recommended_reply

        # Format error message with risk summary
        risk_summary = ", ".join(
            f"{r['type']}({r['severity']})" for r in concerning_risks
        )

        return FailResult(
            error_message=(
                f"Crisis signals detected: {risk_summary}. "
                f"{result.get('rationale', '')}"
            ),
            fix_value=fix_value,
            metadata=failure_metadata,
        )
