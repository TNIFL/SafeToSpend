# 레퍼런스 업데이트 런북

## 목적

건보료/세금 기준값(요율/단가/하한/상한/누진표)이 바뀌는 해에 누락 없이 반영하기 위한 절차입니다.  
앱은 레퍼런스 스냅샷을 기본으로 사용하며, **공식 검증이 실패하면 건보료/세금 숫자 출력을 차단**합니다.

## 업데이트 대상 파일

- `services/reference/nhis_reference.py`
- `services/reference/tax_reference.py`
- `services/official_refs/registry.py`
- `services/official_refs/guard.py`
- `services/official_refs/source_policy.py`
- `configs/parsers/nhis_rates.json`
- `configs/parsers/assets_datasets.json`
- `scripts/refresh_official_snapshots.py`
- `scripts/predeploy_check.py`
- `scripts/suggest_parser_patch.py`
- `docs/REFERENCE_DATA.md`
- `docs/OFFICIAL_REFERENCE_REGISTRY.md`

## 확인해야 하는 공식 소스

1. NHIS 시행령/시행규칙:
   - 보험료율, 점수당 금액, 소득 반영 시기, 금융소득 1,000만 규칙, 전월세 평가식
2. 보건복지부:
   - 장기요양 비율(건강보험료 대비)
3. 고시/생활법령:
   - 월별 건보료 상한/하한
4. 국세청/법제처:
   - 종합소득세 누진세율/누진공제
5. 지방세 안내:
   - 지방소득세 비율(원칙 10%)

## 업데이트 절차

1. 공식 수치 확인 후 `services/reference/*.py` 상수 갱신
2. `docs/REFERENCE_DATA.md` 값/근거 URL/확인일 갱신
3. 공식 스냅샷 배치 갱신:
   - `PYTHONPATH=. .venv/bin/flask --app app refresh-official-snapshots`
   - 산출물:
     - `data/official_snapshots/run_log.json`
     - `data/official_snapshots/manifest.json`
4. 테스트 실행:
   - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_nhis_reference_rules tests.test_tax_reference_rules`
5. 검증 리포트 생성:
   - `PYTHONPATH=. .venv/bin/python scripts/verify_reference_math.py`
6. 생성된 `docs/VERIFICATION_REPORT.md`에서 PASS/FAIL 확인
7. 공식 근거 검증 실행(필수):
   - `PYTHONPATH=. .venv/bin/python scripts/verify_official_refs.py`
   - 결과 확인:
     - `reports/official_ref_audit_YYYYMMDD.md`
     - `data/official_snapshots/manifest.json`
   - `manifest.valid=true`가 아니면 숫자 출력이 차단됩니다.
8. NHIS 통합 스모크로 회귀 확인:
   - `PYTHONPATH=. .venv/bin/python scripts/nhis_integrated_smoke.py`
9. 공식 페이지 변화 감지(권장):
   - `PYTHONPATH=. .venv/bin/python scripts/reference_watchdog.py`
   - 엄격 모드(변화/실패 시 종료코드 1): `PYTHONPATH=. .venv/bin/python scripts/reference_watchdog.py --strict`
10. 배포 전 하드 게이트:
   - `PYTHONPATH=. .venv/bin/python scripts/predeploy_check.py`
   - FAIL이면 배포 중단

## 변화 감지 워치독 운영

- 대상 설정 파일: `data/reference_watch/targets.json`
- 상태 출력 파일: `data/reference_watch/status.json`
- 권장 주기: 하루 1회(cron 또는 배치)
- 경고 기준:
  - `changed=true`: 참조 페이지 핵심 구간 해시 변경
  - `failing=true`: 네트워크 실패/키워드 미검출/읽을 수 없는 페이지
- 관리자 확인: `/admin/ops`의 `공식 기준 감시` 카드에서 최근 상태를 확인
- 관리자 확인: `/admin/ops`의 `공식 스냅샷 갱신` 카드에서 배치 성공/실패를 확인
- 공식 검증 확인: `/admin/ops`의 `공식 기준 검증` 카드에서 차단 상태를 확인
- 변경/실패가 있을 때 패치 초안:
  - `PYTHONPATH=. .venv/bin/python scripts/suggest_parser_patch.py`
- 원칙:
  - 워치독은 운영 보조 도구이며, 앱 런타임 필수 의존 경로로 만들지 않습니다.
  - 자동 코드 수정은 금지하고, 경고 확인 후 사람이 검토해 반영합니다.

## 실패 시 대응

- 테스트 실패: 스냅샷 값/공식 예시 계산식 중 불일치 지점 먼저 수정
- 외부 확인 실패(네트워크/사이트 형식): 런타임 500 없이 유지 + 숫자 출력 차단 + 보고서 기록
- 앱 동작 유지 우선: 검증 실패여도 서비스 런타임은 열려 있어야 하며, 숫자만 숨깁니다.
