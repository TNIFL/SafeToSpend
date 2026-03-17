# Parser Patch Suggestion

- status_path: `/Users/tnifl/Desktop/SafeToSpend/data/reference_watch/status.json`
- generated_at: `2026-03-07T18:23:36`
- 주의: 자동 적용 금지, 사람이 검토 후 설정 파일만 수정하세요.

## asset_home_reference (failing)
- failure_reason: `ConnectionError`
- config_hint: `configs/parsers/assets_datasets.json :: datasets.home.keywords`
- 후보 패턴(최대 3개):

```diff
# configs/parsers/assets_datasets.json :: datasets.home.keywords
```

## asset_vehicle_reference (failing)
- failure_reason: `ConnectionError`
- config_hint: `configs/parsers/assets_datasets.json :: datasets.vehicle.keywords`
- 후보 패턴(최대 3개):

```diff
# configs/parsers/assets_datasets.json :: datasets.vehicle.keywords
```

## mohw_ltc_rate (failing)
- failure_reason: `ConnectionError`
- config_hint: `configs/parsers/nhis_rates.json :: patterns.ltc_ratio / patterns.ltc_optional`
- 후보 패턴(최대 3개):

```diff
# configs/parsers/nhis_rates.json :: patterns.ltc_ratio / patterns.ltc_optional
```

## nhis_health_rate (failing)
- failure_reason: `ConnectionError`
- config_hint: `configs/parsers/nhis_rates.json :: patterns.health_rate`
- 후보 패턴(최대 3개):

```diff
# configs/parsers/nhis_rates.json :: patterns.health_rate
```

## nhis_income_cycle (failing)
- failure_reason: `ConnectionError`
- config_hint: `configs/parsers/nhis_rates.json :: keywords.income_rule`
- 후보 패턴(최대 3개):

```diff
# configs/parsers/nhis_rates.json :: keywords.income_rule
```

## nts_income_tax_table (failing)
- failure_reason: `ConnectionError`
- config_hint: `configs/parsers/nhis_rates.json :: keywords.income_rule`
- 후보 패턴(최대 3개):

```diff
# configs/parsers/nhis_rates.json :: keywords.income_rule
```
