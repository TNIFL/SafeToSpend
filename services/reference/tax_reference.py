from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


@dataclass(frozen=True)
class TaxBracket:
    upper_limit_krw: int
    rate: Decimal
    progressive_deduction_krw: int


@dataclass(frozen=True)
class TaxReferenceSnapshot:
    effective_year: int
    effective_from_date: str
    last_checked_date: str
    income_tax_brackets: tuple[TaxBracket, ...]
    local_income_tax_ratio: Decimal
    sources: dict[str, tuple[str, ...]]

    def as_defaults_dict(self) -> dict[str, Any]:
        return {
            "effective_from_date": self.effective_from_date,
            "last_checked_date": self.last_checked_date,
            "income_tax_brackets": [
                {
                    "upper_limit_krw": int(b.upper_limit_krw),
                    "rate": float(b.rate),
                    "progressive_deduction_krw": int(b.progressive_deduction_krw),
                }
                for b in self.income_tax_brackets
            ],
            "local_income_tax_ratio": float(self.local_income_tax_ratio),
            "sources": {k: tuple(v) for k, v in (self.sources or {}).items()},
        }


TAX_REFERENCE_BY_YEAR: dict[int, TaxReferenceSnapshot] = {
    2026: TaxReferenceSnapshot(
        effective_year=2026,
        effective_from_date="2026-01-01",
        last_checked_date="2026-03-06",
        income_tax_brackets=(
            TaxBracket(14_000_000, Decimal("0.06"), 0),
            TaxBracket(50_000_000, Decimal("0.15"), 1_260_000),
            TaxBracket(88_000_000, Decimal("0.24"), 5_760_000),
            TaxBracket(150_000_000, Decimal("0.35"), 15_440_000),
            TaxBracket(300_000_000, Decimal("0.38"), 19_940_000),
            TaxBracket(500_000_000, Decimal("0.40"), 25_940_000),
            TaxBracket(1_000_000_000, Decimal("0.42"), 35_940_000),
            TaxBracket(10**18, Decimal("0.45"), 65_940_000),
        ),
        local_income_tax_ratio=Decimal("0.10"),
        sources={
            "income_tax_law": (
                "https://www.law.go.kr/LSW/lsLinkCommonInfo.do?ancYnChk=&chrClsCd=010202&lsJoLnkSeq=1019372661",
            ),
            "nts_rate_table": (
                "https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7873&mi=6594",
            ),
            "local_income_tax_ratio": (
                "https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%A7%80%EB%B0%A9%EC%84%B8%EB%B2%95",
            ),
        },
    ),
}


def resolve_tax_reference_year(target_year: int) -> int:
    years = sorted(int(y) for y in TAX_REFERENCE_BY_YEAR.keys())
    if not years:
        return int(target_year)
    chosen = years[0]
    for y in years:
        if y <= int(target_year):
            chosen = y
    return int(chosen)


def get_tax_reference_snapshot(target_year: int) -> TaxReferenceSnapshot:
    year = resolve_tax_reference_year(int(target_year))
    base = TAX_REFERENCE_BY_YEAR.get(year)
    if base is None:
        raise KeyError(f"Tax reference snapshot missing for year={target_year}")
    return replace(base, effective_year=int(target_year))


def calculate_national_income_tax(*, taxable_income_krw: int, target_year: int) -> int:
    taxable = max(0, int(taxable_income_krw or 0))
    if taxable <= 0:
        return 0
    ref = get_tax_reference_snapshot(int(target_year))
    for bracket in ref.income_tax_brackets:
        if taxable <= int(bracket.upper_limit_krw):
            tax = (Decimal(taxable) * Decimal(bracket.rate)) - Decimal(int(bracket.progressive_deduction_krw))
            if tax <= 0:
                return 0
            return int(tax.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return 0


def calculate_local_income_tax(*, national_income_tax_krw: int, target_year: int) -> int:
    national = max(0, int(national_income_tax_krw or 0))
    if national <= 0:
        return 0
    ref = get_tax_reference_snapshot(int(target_year))
    local = Decimal(national) * Decimal(ref.local_income_tax_ratio)
    return int(local.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
