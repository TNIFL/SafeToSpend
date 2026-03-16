# Official Reference Audit

- checked_at: `2026-03-07T09:23:55Z`
- registry_version: `official-refs-2026.03.07`
- target_year: `2026`
- status: **FAIL**
- mismatches: `8`
- network_errors: `0`

## Target Results

| key | status | http | changed | details |
|---|---|---:|---:|---|
| nhis_rate_and_point_value | FAIL | - | no | offline_snapshot_missing; data/official_snapshots/nhis/nhis_enforcement_decree_article44.html |
| nhis_annex4_property_points | FAIL | - | no | offline_snapshot_missing; data/official_snapshots/nhis/nhis_enforcement_decree_annex4_latest.pdf |
| nhis_annex8_rent_formula | FAIL | - | no | offline_snapshot_missing; data/official_snapshots/nhis/nhis_enforcement_rule_annex8_latest.pdf |
| nhis_financial_income_threshold | FAIL | - | no | offline_snapshot_missing; data/official_snapshots/nhis/nhis_enforcement_rule_article44.html |
| nhis_floor_ceiling | FAIL | - | no | offline_snapshot_missing; data/official_snapshots/nhis/nhis_floor_ceiling_notice.html |
| mohw_ltc_ratio | FAIL | - | no | offline_snapshot_missing; data/official_snapshots/mohw/ltc_rate_2026_release.html |
| tax_income_rate_table | FAIL | - | no | offline_snapshot_missing; data/official_snapshots/tax/nts_income_tax_table.html |
| tax_local_income_ratio | FAIL | - | no | offline_snapshot_missing; data/official_snapshots/tax/local_tax_act.html |

## Registry Snapshot

### NHIS
- health_insurance_rate: `0.0719`
- property_point_value: `211.5`
- ltc_ratio_of_health: `0.1314`
- premium_floor_health_only: `20160`
- premium_ceiling_health_only: `4591740`
- property_basic_deduction_krw: `100000000`

### TAX
- local_income_tax_ratio: `0.1`
- bracket_count: `8`

## Failures
- nhis_rate_and_point_value: offline_snapshot_missing
- nhis_annex4_property_points: offline_snapshot_missing
- nhis_annex8_rent_formula: offline_snapshot_missing
- nhis_financial_income_threshold: offline_snapshot_missing
- nhis_floor_ceiling: offline_snapshot_missing
- mohw_ltc_ratio: offline_snapshot_missing
- tax_income_rate_table: offline_snapshot_missing
- tax_local_income_ratio: offline_snapshot_missing
