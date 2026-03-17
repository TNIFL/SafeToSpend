# 공식자료 채널 v1 회귀

main 브랜치에서 공식자료 채널 변경을 점검할 때는 아래 순서로 실행한다.

## 1. baseline 확인

```bash
git status --short --branch
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db heads
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db current
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db upgrade
```

## 2. 공식자료 채널 회귀

```bash
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_official_data_parser_registry \
  tests.test_official_data_parsers \
  tests.test_official_data_upload_routes
```

- `tests/fixtures/official_data/`의 변형 fixture까지 포함해
  - shifted header
  - alias header
  - 날짜/금액 포맷 차이
  - known-source-but-unrecognized
  회귀를 함께 확인한다.

## 3. 정적 검증

```bash
PYTHONPATH=. .venv/bin/python -m py_compile \
  app.py \
  domain/models.py \
  routes/__init__.py \
  routes/web/official_data.py \
  services/official_data_store.py \
  services/official_data_parser_registry.py \
  services/official_data_parsers.py \
  services/official_data_upload.py \
  tests/test_official_data_parser_registry.py \
  tests/test_official_data_parsers.py \
  tests/test_official_data_upload_routes.py
```

## 4. 수동 확인

- `/dashboard/official-data`에서 업로드 진입 가능 여부
- 지원 문서 업로드 후 `반영 가능 / 검토 필요 / 미지원 형식 / 읽기 실패` 표시 여부
- 업로드 결과 화면에서 문서종류, 기관명, 기준일, 읽기상태, 검증상태, 신뢰등급, 핵심 추출값 요약 표시 여부
- 패키지 화면에서 공식자료 업로드 진입 링크가 보이지만, 패키지 v1 범위에는 미포함으로 안내되는지 확인

# 참고자료/추가설명 채널 v1 회귀

main 브랜치에서 참고자료/추가설명 채널 변경을 점검할 때는 아래 순서로 실행한다.

## 1. baseline 확인

```bash
git status --short --branch
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db heads
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db current
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db upgrade
```

## 2. 참고자료 채널 회귀

```bash
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_reference_material_upload_routes
```

## 3. 정적 검증

```bash
PYTHONPATH=. .venv/bin/python -m py_compile \
  app.py \
  domain/models.py \
  routes/__init__.py \
  routes/web/reference_material.py \
  services/reference_material_store.py \
  services/reference_material_upload.py \
  tests/test_reference_material_upload_routes.py
```

## 4. 수동 확인

- `/dashboard/reference-materials`에서 업로드 진입 가능 여부
- 참고자료/추가설명 업로드 후 `참고용`, `자동 반영 안 됨`, `세무사 참고용` 문구 노출 여부
- 공식자료/증빙과 별도 관리 설명이 업로드 화면에 보이는지
- 목록에서 자료종류, 표시제목, 파일명, 업로드시각, 메모 확인 가능 여부

# 교차검증 규칙 v1 회귀

main 브랜치에서 교차검증 규칙 v1 변경을 점검할 때는 아래 순서로 실행한다.

## 1. baseline 확인

```bash
git status --short --branch
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db heads
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db current
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db upgrade
```

## 2. 교차검증/공식자료 회귀

```bash
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_cross_validation \
  tests.test_official_data_upload_routes \
  tests.test_official_data_parsers \
  tests.test_official_data_parser_registry \
  tests.test_tax_package \
  tests.test_package_routes
```

## 3. 정적 검증

```bash
PYTHONPATH=. .venv/bin/python -m py_compile \
  services/cross_validation.py \
  services/official_data_upload.py \
  routes/web/official_data.py \
  tests/test_cross_validation.py \
  tests/test_official_data_upload_routes.py
```

## 4. 수동 확인

- 공식자료 결과 화면에서 `교차검증 결과` 카드가 보이는지
- `일치 / 부분일치 / 참고용 / 재확인필요 / 불일치` 한글 표현이 그대로 노출되는지
- 금액/날짜가 명확한 공식자료는 거래와 비교되고, 비교 대상이 약한 문서는 `참고용`으로 남는지
- 교차검증 결과가 곧바로 `완전 확정`처럼 읽히지 않는지
