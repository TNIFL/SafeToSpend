from __future__ import annotations

from dataclasses import dataclass


TERMS_OF_SERVICE = "terms_of_service"
PRIVACY_POLICY = "privacy_policy"

TERMS_VERSION = "2026-03-draft-1"
PRIVACY_VERSION = "2026-03-draft-1"


@dataclass(frozen=True)
class RequiredConsent:
    document_type: str
    document_version: str


def required_signup_consents() -> tuple[RequiredConsent, ...]:
    return (
        RequiredConsent(
            document_type=TERMS_OF_SERVICE,
            document_version=TERMS_VERSION,
        ),
        RequiredConsent(
            document_type=PRIVACY_POLICY,
            document_version=PRIVACY_VERSION,
        ),
    )
