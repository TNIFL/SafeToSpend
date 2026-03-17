from .registry import (
    ALLOWED_OFFICIAL_DOMAINS,
    OFFICIAL_REFERENCE_YEAR,
    REGISTRY_LAST_REVIEWED_DATE,
    REGISTRY_VERSION,
    get_official_reference_registry,
    get_registry_hash,
    get_verify_targets,
)


def get_official_guard_status(*args, **kwargs):
    from .guard import get_official_guard_status as _impl

    return _impl(*args, **kwargs)


def is_official_refs_valid(*args, **kwargs):
    from .guard import is_official_refs_valid as _impl

    return _impl(*args, **kwargs)

__all__ = [
    "ALLOWED_OFFICIAL_DOMAINS",
    "OFFICIAL_REFERENCE_YEAR",
    "REGISTRY_LAST_REVIEWED_DATE",
    "REGISTRY_VERSION",
    "get_official_reference_registry",
    "get_registry_hash",
    "get_verify_targets",
    "get_official_guard_status",
    "is_official_refs_valid",
]
