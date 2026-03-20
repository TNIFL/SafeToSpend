from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import OperationalError, ProgrammingError

from domain.models import LegalDocumentMetadata


TERMS_OF_SERVICE = "terms_of_service"
PRIVACY_POLICY = "privacy_policy"

TERMS_VERSION = "2026-03-draft-1"
PRIVACY_VERSION = "2026-03-draft-1"
ACTIVE = "active"


@dataclass(frozen=True)
class RequiredConsent:
    document_type: str
    document_version: str


def get_active_legal_document(document_type: str) -> LegalDocumentMetadata | None:
    try:
        return (
            LegalDocumentMetadata.query.filter_by(document_type=document_type, status=ACTIVE)
            .order_by(LegalDocumentMetadata.effective_at.desc(), LegalDocumentMetadata.id.desc())
            .first()
        )
    except (OperationalError, ProgrammingError):
        return None


def required_signup_consents() -> tuple[RequiredConsent, ...]:
    active_terms = get_active_legal_document(TERMS_OF_SERVICE)
    active_privacy = get_active_legal_document(PRIVACY_POLICY)
    return (
        RequiredConsent(
            document_type=TERMS_OF_SERVICE,
            document_version=(active_terms.version if active_terms else TERMS_VERSION),
        ),
        RequiredConsent(
            document_type=PRIVACY_POLICY,
            document_version=(active_privacy.version if active_privacy else PRIVACY_VERSION),
        ),
    )
