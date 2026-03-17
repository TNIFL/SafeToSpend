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
