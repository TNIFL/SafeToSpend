from __future__ import annotations

from dataclasses import dataclass

from services.reference.tax_reference import calculate_local_income_tax, calculate_national_income_tax

REASON_OK = "ok"
REASON_MISSING_TAXABLE_INCOME = "missing_taxable_income"


@dataclass(frozen=True)
class TaxOfficialCoreResult:
    calculable: bool
    reason: str
    taxable_income_annual_krw: int
    national_tax_annual_krw: int
    local_tax_annual_krw: int
    total_tax_annual_krw: int


def _normalize_taxable_income_annual_krw(raw: int | str | float | None) -> int:
    if raw is None:
        return 0
    text = str(raw).replace(",", "").replace("원", "").strip()
    if not text:
        return 0
    try:
        value = int(float(text))
    except Exception:
        return 0
    return max(0, int(value))


def compute_tax_official_core(*, taxable_income_annual_krw: int | None, target_year: int) -> TaxOfficialCoreResult:
    taxable = _normalize_taxable_income_annual_krw(taxable_income_annual_krw)
    if taxable <= 0:
        return TaxOfficialCoreResult(
            calculable=False,
            reason=REASON_MISSING_TAXABLE_INCOME,
            taxable_income_annual_krw=0,
            national_tax_annual_krw=0,
            local_tax_annual_krw=0,
            total_tax_annual_krw=0,
        )

    national = int(calculate_national_income_tax(taxable_income_krw=taxable, target_year=int(target_year)))
    local = int(calculate_local_income_tax(national_income_tax_krw=national, target_year=int(target_year)))
    total = int(max(0, national + local))

    return TaxOfficialCoreResult(
        calculable=True,
        reason=REASON_OK,
        taxable_income_annual_krw=int(taxable),
        national_tax_annual_krw=int(national),
        local_tax_annual_krw=int(local),
        total_tax_annual_krw=int(total),
    )
