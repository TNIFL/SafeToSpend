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

# 가격/플랜 안내 1차 회귀

main 브랜치에서 가격/플랜/구독 안내 변경을 점검할 때는 아래 순서로 실행한다.

## 1. baseline 확인

```bash
git status --short --branch
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db heads
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db current
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db upgrade
```

## 2. 가격/플랜 안내 회귀

```bash
PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_plan_pricing \
  tests.test_billing_routes
```

## 3. 정적 검증

```bash
PYTHONPATH=. .venv/bin/python -m py_compile \
  routes/__init__.py \
  routes/web/billing.py \
  services/plan.py \
  services/billing/constants.py \
  services/billing/pricing.py \
  tests/test_plan_pricing.py \
  tests/test_billing_routes.py
```

## 4. 수동 확인

- `/pricing`에서 무료 / 베이직 / 프로 카드가 보이는지
- `구독 준비 중` 문구가 노출되는지
- `9,900원/월` 잔존 문구가 landing/base에서 사라졌는지
- `/dashboard/billing`이 로그인 전에는 로그인으로 이동하고, 로그인 후에는 안내 화면이 보이는지

# 프로필/문의/관리자 1차 회수 회귀

main 브랜치에서 프로필/문의/관리자 1차 회수를 점검할 때는 아래 순서로 실행한다.

## 1. baseline 확인

```bash
git status --short --branch
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db heads
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db current
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db upgrade
```

## 2. 프로필/문의/관리자 회귀

```bash
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_profile_support_admin_routes
```

## 3. 정적 검증

```bash
PYTHONPATH=. .venv/bin/python -m py_compile \
  core/admin_guard.py \
  routes/__init__.py \
  routes/web/profile.py \
  routes/web/support.py \
  routes/web/admin.py \
  tests/test_profile_support_admin_routes.py
```

## 4. 수동 확인

- `/mypage`가 로그인 전에는 로그인으로 이동하고, 로그인 후에는 이메일/가입일/거래·증빙 요약을 보여주는지
- `/support`에서 문의 저장이 아직 미연결임을 숨기지 않고 안내하는지
- `/admin`이 기본적으로 403을 반환하고, `ADMIN_EMAILS`에 등록된 계정만 접근 가능한지

# 대시보드/네비게이션/UX 1차 회수 회귀

main 브랜치에서 대시보드/네비게이션/UX 1차 회수를 점검할 때는 아래 순서로 실행한다.

## 1. baseline 확인

```bash
git status --short --branch
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db heads
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db current
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db upgrade
```

## 2. UX 회귀

```bash
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_navigation_ux
```

## 3. 정적 검증

```bash
PYTHONPATH=. .venv/bin/python -m py_compile \
  app.py \
  tests/test_navigation_ux.py
```

## 4. 수동 확인

- 로그인 후 상단 네비게이션에서 요약/정리하기/처리함/패키지/공식자료/참고자료/내 계정/문의를 바로 찾을 수 있는지
- 로그인 전에는 없는 기능으로 가는 링크가 보이지 않는지
- overview, dashboard, package 화면에서 현재 있는 기능으로 가는 CTA가 늘었는지
- `알림`, `대사 리포트`, `세금 설정`처럼 아직 없는 링크는 노출되지 않는지

# NHIS 안내형 화면 회귀

main 브랜치에서 NHIS 안내형 화면 변경을 점검할 때는 아래 순서로 실행한다.

## 1. baseline 확인

```bash
git status --short --branch
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db heads
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db current
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db upgrade
```

## 2. NHIS 안내 화면 회귀

```bash
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_nhis_routes \
  tests.test_navigation_ux
```

## 3. 정적 검증

```bash
PYTHONPATH=. .venv/bin/python -m py_compile \
  routes/__init__.py \
  routes/web/nhis.py \
  tests/test_nhis_routes.py \
  tests/test_navigation_ux.py
```

## 4. 수동 확인

- `/dashboard/nhis`가 로그인 전에는 로그인으로 이동하고, 로그인 후에는 안내 화면이 보이는지
- 화면에 `공식자료 업로드`, `참고자료 업로드`, `정리하기`, `세금 보관함`, `세무사 패키지` CTA가 보이는지
- `정확히 계산`, `자동 확정`, `공식 확인 완료` 같은 과장 문구가 없는지
- overview와 dashboard에서 `건보료 안내` 진입 링크를 찾을 수 있는지

# 정리하기/세금 보관함 UX 보강 회귀

main 브랜치에서 정리하기/세금 보관함 UX 보강 블록을 점검할 때는 아래 순서로 실행한다.

## 1. baseline 확인

```bash
git status --short --branch
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db heads
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db current
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' FLASK_APP=app.py .venv/bin/flask db upgrade
```

## 2. review/tax_buffer UX 회귀

```bash
SQLALCHEMY_DATABASE_URI='postgresql+psycopg://tnifl@localhost:5432/safetospend_main_15b018e' PYTHONPATH=. .venv/bin/python -m unittest \
  tests.test_calendar_ux_blocks
```

## 3. 정적 검증

```bash
PYTHONPATH=. .venv/bin/python -m py_compile \
  tests/test_calendar_ux_blocks.py
```

## 4. 수동 확인

- `/dashboard/review`에서 `이번 달 정리 순서`, `자료 보강 경로` 블록이 보이는지
- `/dashboard/tax-buffer`에서 `이 숫자를 이렇게 보세요`, `수치를 보강하는 방법` 블록이 보이는지
- 두 화면에서 `공식자료 업로드`, `참고자료 업로드`, `건보료 안내`, `세무사 패키지` CTA가 노출되는지
- `세금 설정`, `대사 리포트`, `정밀 계산` 같은 비범위 기능이 암시되지 않는지
