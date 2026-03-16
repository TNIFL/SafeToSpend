from __future__ import annotations

from copy import deepcopy
from typing import Any

from services.reference.nhis_reference import (
    NHIS_REFERENCE_BY_YEAR,
    NhisReferenceSnapshot,
    get_nhis_reference_snapshot,
    resolve_nhis_reference_year,
)


def _as_legacy_defaults(snapshot: NhisReferenceSnapshot) -> dict[str, Any]:
    out = snapshot.as_defaults_dict()
    out["effective_year"] = int(snapshot.effective_year)
    return out


NHIS_OFFICIAL_DEFAULTS_BY_YEAR: dict[int, dict[str, Any]] = {
    int(year): _as_legacy_defaults(snap)
    for year, snap in NHIS_REFERENCE_BY_YEAR.items()
}


def resolve_default_year(target_year: int) -> int:
    return resolve_nhis_reference_year(int(target_year))


def get_official_defaults(target_year: int) -> dict[str, Any]:
    snapshot = get_nhis_reference_snapshot(int(target_year))
    return deepcopy(_as_legacy_defaults(snapshot))
