# 코드 전달용 압축 생성 절차

## 목적
- 코드 전달용 ZIP에 런타임 산출물(`uploads`, `*.dump`, 캐시, 로컬 가상환경 등)이 섞이지 않게 한다.
- `.gitignore`만 믿지 않고, 실제 압축 생성 단계에서 제외를 강제한다.

## 금지 포함 대상
- `.env`
- `.env.*` (`.env.example` 제외)
- `uploads/**`
- `reports/**`
- `*.dump`
- `*.sql`
- `*.sqlite3`
- `*.db`
- `__pycache__/**`
- `.pytest_cache/**`
- `.mypy_cache/**`
- `.ruff_cache/**`
- `.DS_Store`
- `.venv/**`
- `node_modules/**`
- `tmp/**`
- `logs/**`

## 권장 절차
1. 금지 경로 존재 여부 먼저 확인
```bash
PYTHONPATH=. .venv/bin/python scripts/export_code_bundle.py --dry-run --fail-if-forbidden-found
```

금지 경로가 repo 안에 남아 있어도 코드 전달용 ZIP은 생성할 수 있지만, 이 단계에서 먼저 확인하고 전달 대상에서 빼는지 검토한다.

2. 코드 전달용 ZIP 생성
```bash
PYTHONPATH=. .venv/bin/python scripts/export_code_bundle.py
```

3. 필요 시 출력 위치를 명시
```bash
PYTHONPATH=. .venv/bin/python scripts/export_code_bundle.py --output /tmp/SafeToSpend_code_manual.zip
```

## 검증 포인트
- 결과 ZIP은 repo 밖 경로에 생성한다.
- 출력 요약 JSON의 `excluded_samples`에 `uploads/`, `reports/rehearsals/*.dump`, 캐시 경로가 보이면 정상이다.
- 출력 JSON의 `archive_verified=true`가 보이면 ZIP 내부 재검증까지 끝난 상태다.
- 결과 ZIP 안에는 런타임 데이터가 들어가면 안 된다.

## 주의
- 앱 기능용 증빙 백업 ZIP(`routes/web/vault.py`의 전체 백업)은 사용자 데이터 백업 기능이다. 코드 전달용 ZIP과 목적이 다르므로 혼용하지 않는다.
- `reports/**`는 리허설 dump, 스모크 결과, 실데이터 기반 분석 산출물이 섞일 수 있으므로 코드 전달 ZIP에 포함하지 않는다.
- 운영/개발자가 수동으로 Finder나 압축 유틸을 쓸 때도 같은 제외 정책을 따라야 한다.
