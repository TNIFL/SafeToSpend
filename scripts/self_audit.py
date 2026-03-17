from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.exc import CompileError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class CheckResult:
    key: str
    status: str  # PASS / FAIL / WARN / SKIP
    summary: str
    detail: str = ""
    hint: str = ""


class SelfAudit:
    def __init__(self, run_upgrade: bool = True, no_db: bool = False, strict_db: bool = False) -> None:
        self.run_upgrade = run_upgrade
        self.no_db = no_db
        self.strict_db = strict_db
        self.results: list[CheckResult] = []
        self.app = None
        self.app_error = ""

    def add(self, key: str, status: str, summary: str, detail: str = "", hint: str = "") -> None:
        self.results.append(CheckResult(key=key, status=status, summary=summary, detail=detail, hint=hint))

    def _short_err(self, exc: BaseException) -> str:
        return f"{type(exc).__name__}: {exc}"

    def _read_text(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8", errors="ignore")

    def _run_cmd(self, args: list[str], timeout_sec: int = 120) -> tuple[int, str, str]:
        proc = subprocess.run(
            args,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=os.environ.copy(),
        )
        return proc.returncode, (proc.stdout or ""), (proc.stderr or "")

    def _is_db_unavailable(self, text: str) -> bool:
        t = (text or "").lower()
        hints = [
            "operationalerror",
            "connection to server",
            "could not connect",
            "operation not permitted",
            "failed to establish a new connection",
            "connection refused",
        ]
        return any(h in t for h in hints)

    def _function_block(self, src: str, name: str) -> str:
        pattern = rf"def\s+{re.escape(name)}\s*\(.*?(?=\n\s*@bp\.(?:get|post|route)\(|\Z)"
        m = re.search(pattern, src, flags=re.S)
        return m.group(0) if m else ""

    def check_app_boot_import(self) -> None:
        try:
            from app import create_app

            self.app = create_app()
            self.app.config.update(TESTING=True)
            self.add("A1_APP_IMPORT", "PASS", "앱 import/create_app 성공")
        except Exception as exc:
            self.app = None
            self.app_error = self._short_err(exc)
            self.add(
                "A1_APP_IMPORT",
                "FAIL",
                "앱 import/create_app 실패",
                detail=self.app_error,
                hint="app.py 초기화(create_app)와 환경변수(SQLALCHEMY_DATABASE_URI, SECRET_KEY) 구성을 확인하세요.",
            )

    def check_migrations(self) -> None:
        if self.no_db:
            self.add("B1_DB_HEADS", "SKIP", "--no-db 모드라 마이그레이션 점검을 건너뛰었어요.")
            self.add("B2_DB_UPGRADE", "SKIP", "--no-db 모드라 db upgrade 점검을 건너뛰었어요.")
            return

        heads_cmd = [sys.executable, "-m", "flask", "--app", "app", "db", "heads"]
        code, out, err = self._run_cmd(heads_cmd, timeout_sec=120)
        if code != 0:
            full = f"{err}\n{out}".strip()
            if self._is_db_unavailable(full) and not self.strict_db:
                self.add(
                    "B1_DB_HEADS",
                    "SKIP",
                    "DB 연결 제약으로 head 점검을 건너뛰었어요.",
                    detail=full[:500],
                    hint="로컬/운영 환경에서는 DB 연결 후 다시 실행해 주세요.",
                )
                return
            self.add(
                "B1_DB_HEADS",
                "FAIL",
                "마이그레이션 head 조회 실패",
                detail=(err.strip() or out.strip())[:700],
                hint="flask db heads 실행 환경(DB 연결/설정)을 확인하세요.",
            )
            return

        head_ids = re.findall(r"([0-9a-f]{6,})\s+\(head\)", out, flags=re.I)
        head_count = len(head_ids)
        if head_count == 1:
            self.add("B1_DB_HEADS", "PASS", f"마이그레이션 head 1개 확인({head_ids[0]})")
        elif head_count == 0:
            self.add(
                "B1_DB_HEADS",
                "WARN",
                "head 정보를 파싱하지 못했어요",
                detail=out.strip()[:500],
                hint="alembic output 형식이 바뀌었는지 확인하세요.",
            )
        else:
            self.add(
                "B1_DB_HEADS",
                "FAIL",
                f"마이그레이션 head가 {head_count}개입니다.",
                detail=", ".join(head_ids[:5]),
                hint="브랜치된 마이그레이션을 merge해 단일 head로 정리하세요.",
            )

        if not self.run_upgrade:
            self.add("B2_DB_UPGRADE", "SKIP", "db upgrade 점검은 --no-upgrade로 건너뛰었어요.")
            return

        up_cmd = [sys.executable, "-m", "flask", "--app", "app", "db", "upgrade"]
        code2, out2, err2 = self._run_cmd(up_cmd, timeout_sec=180)
        if code2 == 0:
            self.add("B2_DB_UPGRADE", "PASS", "flask db upgrade 성공(또는 이미 최신)")
        else:
            detail = (err2.strip() or out2.strip())[:700]
            if self._is_db_unavailable(detail) and not self.strict_db:
                self.add(
                    "B2_DB_UPGRADE",
                    "SKIP",
                    "DB 연결 제약으로 db upgrade 점검을 건너뛰었어요.",
                    detail=detail,
                    hint="로컬/운영 환경에서는 DB 연결 후 다시 실행해 주세요.",
                )
                return
            self.add(
                "B2_DB_UPGRADE",
                "FAIL",
                "flask db upgrade 실패",
                detail=detail,
                hint="DB 접속 권한/네트워크/마이그레이션 스크립트 오류를 확인하세요.",
            )

    def check_route_smoke(self) -> None:
        if self.app is None:
            self.add("C1_ROUTE_SMOKE", "SKIP", "앱 생성 실패로 라우트 스모크를 건너뛰었어요.")
            return

        client = self.app.test_client()
        paths = [
            ("/", True),
            ("/preview", False),
            ("/inbox/import", True),
            ("/dashboard/review", True),
            ("/dashboard/tax-buffer", True),
            ("/dashboard/package", True),
        ]

        statuses: list[str] = []
        failed = False
        warned = False
        for path, required in paths:
            try:
                res = client.get(path, follow_redirects=False)
                code = int(res.status_code)
                statuses.append(f"{path}:{code}")
                if code >= 500:
                    failed = True
                elif code == 404 and required:
                    failed = True
                elif code == 404 and not required:
                    warned = True
            except Exception as exc:
                msg = self._short_err(exc)
                if self._is_db_unavailable(msg) and not self.strict_db:
                    warned = True
                    statuses.append(f"{path}:SKIP(DB_UNAVAILABLE)")
                else:
                    failed = True
                    statuses.append(f"{path}:ERR({type(exc).__name__})")

        if failed:
            self.add(
                "C1_ROUTE_SMOKE",
                "FAIL",
                "비로그인 라우트 스모크에서 오류가 발견됐어요.",
                detail=" | ".join(statuses)[:700],
                hint="해당 라우트의 블루프린트 등록/권한 분기/템플릿 렌더를 확인하세요.",
            )
        elif warned:
            self.add(
                "C1_ROUTE_SMOKE",
                "WARN",
                "비로그인 라우트 스모크 완료(일부 optional 라우트 404)",
                detail=" | ".join(statuses)[:700],
            )
        else:
            self.add("C1_ROUTE_SMOKE", "PASS", "비로그인 라우트 스모크 통과", detail=" | ".join(statuses))

        # 로그인 세션 스모크(테스트 유저가 있을 때만)
        if self.no_db:
            self.add("C2_ROUTE_SMOKE_AUTH", "SKIP", "--no-db 모드라 로그인 스모크를 건너뛰었어요.")
            return
        try:
            with self.app.app_context():
                from domain.models import User

                user = User.query.filter_by(email="test+local@safetospend.local").first()
            if not user:
                self.add("C2_ROUTE_SMOKE_AUTH", "SKIP", "테스트 유저가 없어 로그인 스모크를 건너뛰었어요.")
                return
            with client.session_transaction() as sess:
                sess["user_id"] = int(user.id)
                sess.permanent = True
            auth_paths = ["/dashboard/review", "/dashboard/tax-buffer", "/dashboard/package", "/dashboard/profile"]
            auth_status = []
            auth_fail = False
            for path in auth_paths:
                r = client.get(path, follow_redirects=False)
                auth_status.append(f"{path}:{r.status_code}")
                if int(r.status_code) >= 500:
                    auth_fail = True
            if auth_fail:
                self.add(
                    "C2_ROUTE_SMOKE_AUTH",
                    "FAIL",
                    "로그인 상태 라우트 스모크에서 500이 발생했어요.",
                    detail=" | ".join(auth_status),
                    hint="세션 기반 권한 분기와 사용자 데이터 조회 경로를 점검하세요.",
                )
            else:
                self.add("C2_ROUTE_SMOKE_AUTH", "PASS", "로그인 상태 라우트 스모크 통과", detail=" | ".join(auth_status))
        except Exception as exc:
            if self._is_db_unavailable(self._short_err(exc)) and not self.strict_db:
                self.add(
                    "C2_ROUTE_SMOKE_AUTH",
                    "SKIP",
                    "DB 연결 제약으로 로그인 스모크를 건너뛰었어요.",
                    detail=self._short_err(exc),
                )
                return
            self.add(
                "C2_ROUTE_SMOKE_AUTH",
                "WARN",
                "로그인 스모크를 완료하지 못했어요.",
                detail=self._short_err(exc),
                hint="테스트 유저 시드 상태 또는 DB 연결 상태를 확인하세요.",
            )

    def check_assets_quiz_regression_isolated(self) -> None:
        """
        최근 실제 이슈(집/차 다중 입력, 이전 버튼 동작)를
        가능한 경우 실제 DB(현재 app 컨텍스트)에서 회귀 점검한다.
        DB 실행이 어려우면 정적 점검으로 대체한다.
        """
        def _fallback_static(detail: str, warn: bool = True) -> None:
            try:
                profile_src = self._read_text("routes/web/profile.py")
                assets_src = self._read_text("services/assets_profile.py")
                tpl_src = self._read_text("templates/assets_quiz.html")

                back_ok = ("if step == 5" in profile_src) and ("prev_step = 3" in profile_src)
                home_loop_ok = "for idx in range(1, home_count + 1)" in assets_src
                car_loop_ok = "for idx in range(1, car_count + 1)" in assets_src
                slot_ok = (
                    ("for idx in range(1, 4)" in tpl_src)
                    and ('data-home-row="{{ idx }}"' in tpl_src)
                    and ('name="home_address_{{ idx }}"' in tpl_src)
                    and ('data-car-row="{{ idx }}"' in tpl_src)
                    and ('name="car_brand_{{ idx }}"' in tpl_src)
                )
                if back_ok and home_loop_ok and car_loop_ok and slot_ok:
                    self.add(
                        "C3_ASSETS_QUIZ_FLOW",
                        ("WARN" if warn else "PASS"),
                        ("런타임 점검은 건너뛰고 정적 회귀 점검은 통과했어요." if warn else "정적 회귀 점검 통과"),
                        detail=detail,
                        hint=("PostgreSQL 연결 가능 환경에서는 런타임 회귀 점검으로 한 번 더 확인해 주세요." if warn else ""),
                    )
                else:
                    self.add(
                        "C3_ASSETS_QUIZ_FLOW",
                        "FAIL",
                        "자산 퀴즈 회귀 정적 점검에서 누락이 있어요.",
                        detail=detail,
                        hint="assets_quiz 이전 버튼/다중 반복 루프/UI 슬롯을 다시 확인하세요.",
                    )
            except Exception as inner_exc:
                self.add(
                    "C3_ASSETS_QUIZ_FLOW",
                    "FAIL",
                    "자산 퀴즈 회귀 점검 대체 검사도 실패했어요.",
                    detail=f"{detail} | fallback={self._short_err(inner_exc)}",
                    hint="자산 퀴즈 관련 라우트/서비스/템플릿을 확인하세요.",
                )

        if self.no_db:
            _fallback_static("no-db 모드")
            return

        if self.app is None:
            _fallback_static("app 미생성", warn=True)
            return

        user_id: int | None = None
        tmp_email = f"audit+assets+{int(os.times().elapsed * 1000000)}@safetospend.local"
        try:
            from core.extensions import db
            from domain.models import AssetItem, AssetProfile, User
            try:
                from domain.models import NhisUserProfile  # type: ignore
            except Exception:
                NhisUserProfile = None  # type: ignore

            with self.app.app_context():
                user = User(email="audit+assets@safetospend.local")
                user.email = tmp_email
                user.set_password("Test1234!")
                db.session.add(user)
                db.session.commit()
                user_id = int(user.id)

            client = self.app.test_client()
            with client.session_transaction() as sess:
                sess["user_id"] = user_id
                sess["_csrf_token"] = "audit-csrf-token"
                sess.permanent = True

            # step2: own
            r2 = client.post(
                "/dashboard/assets/quiz?month=2026-03",
                data={
                    "csrf_token": "audit-csrf-token",
                    "step": "2",
                    "action": "next",
                    "housing_mode": "own",
                },
                follow_redirects=False,
            )
            if int(r2.status_code) not in {302, 303}:
                self.add(
                    "C3_ASSETS_QUIZ_FLOW",
                    "FAIL",
                    "자산 퀴즈 step2 저장이 실패했어요.",
                    detail=f"status={r2.status_code}",
                    hint="assets_quiz step2 저장 분기와 csrf/session 처리를 확인하세요.",
                )
                return

            # step3: home_count=2
            r3 = client.post(
                "/dashboard/assets/quiz?month=2026-03",
                data={
                    "csrf_token": "audit-csrf-token",
                    "step": "3",
                    "action": "next",
                    "home_count": "2",
                    "home_address_1": "서울 강남구",
                    "home_type_1": "apartment",
                    "home_area_sqm_1": "84",
                    "home_address_2": "경기 시흥시",
                    "home_type_2": "villa",
                    "home_area_sqm_2": "59",
                },
                follow_redirects=False,
            )
            if int(r3.status_code) not in {302, 303}:
                self.add(
                    "C3_ASSETS_QUIZ_FLOW",
                    "FAIL",
                    "자산 퀴즈 step3 저장이 실패했어요.",
                    detail=f"status={r3.status_code}",
                    hint="home_count 다중 입력 처리(save_assets_quiz_step step3)를 확인하세요.",
                )
                return

            # step5 back should go to step3 (own mode)
            r_back = client.post(
                "/dashboard/assets/quiz?month=2026-03",
                data={
                    "csrf_token": "audit-csrf-token",
                    "step": "5",
                    "action": "back",
                },
                follow_redirects=False,
            )
            loc = str(r_back.headers.get("Location") or "")
            if ("step=3" not in loc) or int(r_back.status_code) not in {302, 303}:
                self.add(
                    "C3_ASSETS_QUIZ_FLOW",
                    "FAIL",
                    "자가 모드에서 이전 버튼이 step3으로 돌아가지 않아요.",
                    detail=f"status={r_back.status_code}, location={loc}",
                    hint="assets_quiz action=back 분기(step5+own)를 확인하세요.",
                )
                return

            # step5: has_car=yes, car_count=2
            r5 = client.post(
                "/dashboard/assets/quiz?month=2026-03",
                data={
                    "csrf_token": "audit-csrf-token",
                    "step": "5",
                    "action": "next",
                    "has_car": "yes",
                    "car_count": "2",
                    "car_brand_1": "현대",
                    "car_model_1": "아반떼",
                    "car_year_1": "2021",
                    "car_brand_2": "기아",
                    "car_model_2": "K5",
                    "car_year_2": "2022",
                },
                follow_redirects=False,
            )
            if int(r5.status_code) not in {302, 303}:
                self.add(
                    "C3_ASSETS_QUIZ_FLOW",
                    "FAIL",
                    "자산 퀴즈 step5 저장이 실패했어요.",
                    detail=f"status={r5.status_code}",
                    hint="car_count 다중 입력 처리(save_assets_quiz_step step5)를 확인하세요.",
                )
                return

            with self.app.app_context():
                homes = AssetItem.query.filter_by(user_pk=user_id, kind="home").all()
                home_labels = {str(h.label or "").strip() for h in homes}
                cars = AssetItem.query.filter_by(user_pk=user_id, kind="car").all()
                car_labels = {str(c.label or "").strip() for c in cars}

            home_ok = ("보유주택 1" in home_labels) and ("보유주택 2" in home_labels)
            car_ok = ("차량 1" in car_labels) and ("차량 2" in car_labels)
            if not (home_ok and car_ok):
                self.add(
                    "C3_ASSETS_QUIZ_FLOW",
                    "FAIL",
                    "다중 자산 저장 라벨이 누락됐어요.",
                    detail=f"homes={sorted(home_labels)} cars={sorted(car_labels)}",
                    hint="다중 입력 라벨(보유주택 N/차량 N) 생성/유지 로직을 확인하세요.",
                )
                return

            # UI slots should include 2/3 fields in quiz pages
            g3 = client.get("/dashboard/assets/quiz?step=3&month=2026-03", follow_redirects=False)
            h3 = g3.get_data(as_text=True) if int(g3.status_code) < 500 else ""
            g5 = client.get("/dashboard/assets/quiz?step=5&month=2026-03", follow_redirects=False)
            h5 = g5.get_data(as_text=True) if int(g5.status_code) < 500 else ""
            ui_ok = (
                ('name="home_address_2"' in h3)
                and ('name="home_address_3"' in h3)
                and ('name="car_brand_2"' in h5)
                and ('name="car_brand_3"' in h5)
            )
            if not ui_ok:
                self.add(
                    "C3_ASSETS_QUIZ_FLOW",
                    "FAIL",
                    "퀴즈 다중 입력 UI 슬롯이 부족해요.",
                    hint="assets_quiz 템플릿에서 home/car 1~3 슬롯 렌더를 확인하세요.",
                )
                return

            # save_main에서 대표 입력이 비어 있어도 다중 항목 1번이 덮어써지지 않는지 점검
            r_main = client.post(
                "/dashboard/assets?month=2026-03&skip_quiz=1",
                data={
                    "csrf_token": "audit-csrf-token",
                    "month": "2026-03",
                    "housing_mode": "own",
                    "home_address": "",
                    "home_type": "",
                    "home_area_sqm": "",
                    "has_car": "yes",
                    "car_brand": "",
                    "car_model": "",
                    "car_year": "",
                },
                follow_redirects=False,
            )
            if int(r_main.status_code) not in {302, 303}:
                self.add(
                    "C3_ASSETS_QUIZ_FLOW",
                    "FAIL",
                    "자산 메인 저장(save_main) 요청이 실패했어요.",
                    detail=f"status={r_main.status_code}",
                    hint="assets_page POST save_main 분기와 CSRF 처리를 확인하세요.",
                )
                return
            with self.app.app_context():
                home1 = (
                    AssetItem.query.filter(
                        AssetItem.user_pk == int(user_id),
                        AssetItem.kind == "home",
                        AssetItem.label == "보유주택 1",
                    )
                    .order_by(AssetItem.updated_at.desc(), AssetItem.id.desc())
                    .first()
                )
                car1 = (
                    AssetItem.query.filter(
                        AssetItem.user_pk == int(user_id),
                        AssetItem.kind == "car",
                        AssetItem.label == "차량 1",
                    )
                    .order_by(AssetItem.updated_at.desc(), AssetItem.id.desc())
                    .first()
                )
                home1_addr = str((home1.input_json or {}).get("address_text") or "").strip() if home1 else ""
                car1_brand = str((car1.input_json or {}).get("brand") or "").strip() if car1 else ""
            if (not home1_addr) or (not car1_brand):
                self.add(
                    "C3_ASSETS_QUIZ_FLOW",
                    "FAIL",
                    "자산 메인 저장 시 다중 1번 항목이 비어 있는 값으로 덮어써졌어요.",
                    detail=f"home1_address={home1_addr!r}, car1_brand={car1_brand!r}",
                    hint="save_assets_page에서 다중 항목+빈 대표 입력 덮어쓰기 방지 로직을 확인하세요.",
                )
                return

            self.add(
                "C3_ASSETS_QUIZ_FLOW",
                "PASS",
                "자산 퀴즈 회귀(집/차 다중 입력, 이전 버튼) 점검 통과",
                detail=f"home_labels={sorted(home_labels)} car_labels={sorted(car_labels)}",
            )
        except CompileError as exc:
            _fallback_static(self._short_err(exc), warn=True)
        except Exception as exc:
            err = self._short_err(exc)
            if self._is_db_unavailable(err) and (not self.strict_db):
                _fallback_static(err, warn=True)
            else:
                self.add(
                    "C3_ASSETS_QUIZ_FLOW",
                    "FAIL",
                    "자산 퀴즈 회귀 점검 중 오류가 발생했어요.",
                    detail=err,
                    hint="assets_quiz 라우트/서비스/템플릿 회귀를 확인하세요.",
                )
        finally:
            if self.app is not None and user_id:
                try:
                    from core.extensions import db
                    from domain.models import AssetItem, AssetProfile, User
                    try:
                        from domain.models import NhisUserProfile  # type: ignore
                    except Exception:
                        NhisUserProfile = None  # type: ignore
                    with self.app.app_context():
                        AssetItem.query.filter(AssetItem.user_pk == int(user_id)).delete(synchronize_session=False)
                        AssetProfile.query.filter(AssetProfile.user_pk == int(user_id)).delete(synchronize_session=False)
                        if NhisUserProfile is not None:
                            NhisUserProfile.query.filter(NhisUserProfile.user_pk == int(user_id)).delete(synchronize_session=False)  # type: ignore
                        User.query.filter(User.id == int(user_id)).delete(synchronize_session=False)
                        db.session.commit()
                except Exception:
                    try:
                        from core.extensions import db
                        db.session.rollback()
                    except Exception:
                        pass

    def check_p0_static(self) -> None:
        # Settings alias
        try:
            import domain.models as models

            ok = hasattr(models, "Settings") and hasattr(models, "SafeToSpendSettings")
            same = bool(ok and models.Settings is models.SafeToSpendSettings)
            if same:
                self.add("D1_SETTINGS_ALIAS", "PASS", "Settings alias가 SafeToSpendSettings에 정상 연결돼요.")
            else:
                self.add(
                    "D1_SETTINGS_ALIAS",
                    "FAIL",
                    "Settings alias 연결이 불완전해요.",
                    hint="domain/models.py에서 Settings = SafeToSpendSettings 호환 alias를 확인하세요.",
                )
        except Exception as exc:
            self.add("D1_SETTINGS_ALIAS", "FAIL", "Settings alias 점검 실패", detail=self._short_err(exc))

        # Evidence upload old-file deletion order
        try:
            src = self._read_text("routes/web/calendar/review.py")
            block = self._function_block(src, "review_evidence_upload")
            if not block:
                self.add("D2_EVIDENCE_DELETE_ORDER", "FAIL", "review_evidence_upload 함수를 찾지 못했어요.")
            else:
                pos_commit = block.find("db.session.commit()")
                pos_del_guard = block.find("if old_file_key")
                pos_del_call = block.find("delete_physical_file(old_file_key)")
                if pos_commit >= 0 and pos_del_guard > pos_commit and (pos_del_call < 0 or pos_del_call > pos_commit):
                    self.add("D2_EVIDENCE_DELETE_ORDER", "PASS", "기존 증빙 파일 삭제가 커밋 이후에 실행돼요.")
                else:
                    self.add(
                        "D2_EVIDENCE_DELETE_ORDER",
                        "FAIL",
                        "기존 증빙 파일 삭제 순서가 안전하지 않을 수 있어요.",
                        hint="새 파일 저장+DB 커밋 성공 이후에만 old_file_key 삭제하도록 점검하세요.",
                    )
        except Exception as exc:
            self.add("D2_EVIDENCE_DELETE_ORDER", "FAIL", "증빙 업로드 삭제 순서 점검 실패", detail=self._short_err(exc))

        # Receipt parse exception handling with normalize_receipt_error
        try:
            src = self._read_text("routes/web/calendar/review.py")
            fn_names = ["review_evidence_parse_page", "receipt_confirm_page"]
            missing: list[str] = []
            for fn in fn_names:
                block = self._function_block(src, fn)
                if not block:
                    missing.append(f"{fn}:missing")
                    continue
                if ("except Exception" not in block) or ("normalize_receipt_error" not in block):
                    missing.append(f"{fn}:handler")
            if not missing:
                self.add("D3_RECEIPT_PARSE_GUARD", "PASS", "영수증 파싱 경로에 예외 변환/친화 메시지 처리가 있어요.")
            else:
                self.add(
                    "D3_RECEIPT_PARSE_GUARD",
                    "FAIL",
                    "영수증 파싱 경로 일부에 예외 가드가 부족해요.",
                    detail=", ".join(missing),
                    hint="parse_receipt 호출부를 try/except + normalize_receipt_error로 통일하세요.",
                )
        except Exception as exc:
            self.add("D3_RECEIPT_PARSE_GUARD", "FAIL", "영수증 파싱 가드 점검 실패", detail=self._short_err(exc))

        # Upload config key bridge
        try:
            app_src = self._read_text("app.py")
            vault_src = self._read_text("services/evidence_vault.py")
            has_max_upload = "MAX_UPLOAD_BYTES" in app_src
            has_fallback = ("EVIDENCE_MAX_BYTES" in vault_src) and ("MAX_UPLOAD_BYTES" in vault_src)
            if has_max_upload and has_fallback:
                self.add("D4_UPLOAD_LIMIT_BRIDGE", "PASS", "EVIDENCE_MAX_BYTES ↔ MAX_UPLOAD_BYTES 브릿지 구성이 있어요.")
            else:
                self.add(
                    "D4_UPLOAD_LIMIT_BRIDGE",
                    "FAIL",
                    "업로드 제한 키 브릿지 구성이 누락됐을 수 있어요.",
                    hint="app.py의 MAX_UPLOAD_BYTES 설정과 evidence_vault fallback 로직을 맞춰주세요.",
                )
        except Exception as exc:
            self.add("D4_UPLOAD_LIMIT_BRIDGE", "FAIL", "업로드 제한 키 점검 실패", detail=self._short_err(exc))

        # Assets inline actions fallback (JS 실패 시 submit 가능해야 함)
        try:
            tpl = self._read_text("templates/assets.html")
            submit_buttons_ok = (
                'name="action"' in tpl
                and 'data-inline-submit="update_item"' in tpl
                and 'data-inline-submit="delete_item"' in tpl
                and 'data-inline-submit="add_home_item"' in tpl
                and 'data-inline-submit="add_car_item"' in tpl
            )
            js_guard_ok = "e.preventDefault();" in tpl
            if submit_buttons_ok and js_guard_ok:
                self.add(
                    "D5_ASSETS_INLINE_FALLBACK",
                    "PASS",
                    "자산 인라인 버튼이 JS 실패 시에도 서버 폴백 제출이 가능해요.",
                )
            else:
                self.add(
                    "D5_ASSETS_INLINE_FALLBACK",
                    "FAIL",
                    "자산 인라인 버튼 폴백 구성이 부족해요.",
                    hint="assets.html 인라인 버튼(type=submit+action)과 클릭 핸들러 preventDefault를 확인하세요.",
                )
        except Exception as exc:
            self.add(
                "D5_ASSETS_INLINE_FALLBACK",
                "FAIL",
                "자산 인라인 폴백 점검 실패",
                detail=self._short_err(exc),
            )

    def check_nhis_engine_sanity(self) -> None:
        try:
            from services.nhis_estimator import estimate_nhis_current_vs_november, estimate_nhis_monthly_dict

            profile = {
                "member_type": "regional",
                "target_month": "2026-03",
                "annual_income_krw": 48_000_000,
                "non_salary_annual_income_krw": 48_000_000,
                "property_tax_base_total_krw": 180_000_000,
                "rent_deposit_krw": 30_000_000,
                "rent_monthly_krw": 700_000,
                "household_has_others": False,
            }
            est = estimate_nhis_monthly_dict(profile, None)
            cmp_out = estimate_nhis_current_vs_november(profile, None)

            required_keys = (
                "income_points",
                "property_points",
                "health_est_krw",
                "ltc_est_krw",
                "total_est_krw",
                "income_year_applied",
                "property_year_applied",
                "caps_applied",
                "floors_applied",
            )
            has_keys = all(k in est for k in required_keys)
            non_negative = int(est.get("total_est_krw") or 0) >= 0
            compare_ok = ("current" in cmp_out) and ("november" in cmp_out) and ("diff_krw" in cmp_out)
            tiny_guard = int(est.get("total_est_krw") or 0) >= 10_000

            if has_keys and non_negative and compare_ok and tiny_guard:
                self.add(
                    "G1_NHIS_ENGINE_SANITY",
                    "PASS",
                    "NHIS 엔진 기본 분해/현재vs11월 산출이 정상이에요.",
                )
            else:
                self.add(
                    "G1_NHIS_ENGINE_SANITY",
                    "FAIL",
                    "NHIS 엔진 기본 산출에 이상 징후가 있어요.",
                    detail=(
                        f"has_keys={has_keys}, non_negative={non_negative}, "
                        f"compare_ok={compare_ok}, tiny_guard={tiny_guard}, "
                        f"total={est.get('total_est_krw')}"
                    ),
                    hint="services/nhis_estimator.py 산식 분해 키/하한 처리/비정상 저점수 경고 경로를 확인하세요.",
                )
        except Exception as exc:
            self.add(
                "G1_NHIS_ENGINE_SANITY",
                "FAIL",
                "NHIS 엔진 점검 실패",
                detail=self._short_err(exc),
                hint="services/nhis_estimator.py import/산식 예외 경로를 확인하세요.",
            )

    def check_security_basics(self) -> None:
        # redirect next guard static/runtime
        try:
            from core.security import sanitize_next_url

            ok_external = sanitize_next_url("https://evil.com", "/dashboard") == "/dashboard"
            ok_scheme_less = sanitize_next_url("//evil.com/path", "/dashboard") == "/dashboard"
            ok_internal = sanitize_next_url("/dashboard/review?tab=required", "/dashboard").startswith("/dashboard/review")
            if ok_external and ok_scheme_less and ok_internal:
                self.add("E1_NEXT_GUARD", "PASS", "next 리다이렉트가 내부 경로만 허용돼요.")
            else:
                self.add(
                    "E1_NEXT_GUARD",
                    "FAIL",
                    "next 리다이렉트 가드가 불완전해요.",
                    hint="sanitize_next_url 외부 URL/스킴 없는 외부 경로 차단 로직을 확인하세요.",
                )
        except Exception as exc:
            self.add("E1_NEXT_GUARD", "FAIL", "next 리다이렉트 가드 점검 실패", detail=self._short_err(exc))

        # csrf static check
        try:
            app_src = self._read_text("app.py")
            has_before_request = "def enforce_web_csrf" in app_src
            has_validate = "is_valid_csrf_token" in app_src
            if has_before_request and has_validate:
                self.add("E2_CSRF_GUARD", "PASS", "웹 상태 변경 요청 CSRF 가드가 존재해요.")
            else:
                self.add(
                    "E2_CSRF_GUARD",
                    "FAIL",
                    "CSRF 가드 구성이 부족해요.",
                    hint="웹 POST/PUT/PATCH/DELETE 경로의 CSRF 검증 훅을 확인하세요.",
                )
        except Exception as exc:
            self.add("E2_CSRF_GUARD", "FAIL", "CSRF 가드 점검 실패", detail=self._short_err(exc))

        # session cookie flags
        if self.app is None:
            self.add("E3_SESSION_COOKIE_FLAGS", "SKIP", "앱 생성 실패로 세션 쿠키 설정 점검을 건너뛰었어요.")
        else:
            cfg = self.app.config
            has_http_only = bool(cfg.get("SESSION_COOKIE_HTTPONLY") is True)
            has_samesite = str(cfg.get("SESSION_COOKIE_SAMESITE") or "").strip() != ""
            has_lifetime = bool(cfg.get("PERMANENT_SESSION_LIFETIME"))
            if has_http_only and has_samesite and has_lifetime:
                self.add("E3_SESSION_COOKIE_FLAGS", "PASS", "세션 쿠키 기본 보안 플래그가 설정돼요.")
            else:
                self.add(
                    "E3_SESSION_COOKIE_FLAGS",
                    "FAIL",
                    "세션 쿠키 보안 플래그가 일부 누락됐어요.",
                    hint="SESSION_COOKIE_HTTPONLY/SAMESITE/PERMANENT_SESSION_LIFETIME 설정을 확인하세요.",
                )

    def check_route_duplicates(self) -> None:
        if self.app is None:
            self.add("F1_ROUTE_DUPLICATES", "SKIP", "앱 생성 실패로 라우트 중복 점검을 건너뛰었어요.")
            return

        try:
            from collections import defaultdict

            by_rule_method: dict[tuple[str, str], set[str]] = defaultdict(set)
            for rule in self.app.url_map.iter_rules():
                methods = sorted(m for m in (rule.methods or set()) if m not in {"HEAD", "OPTIONS"})
                for m in methods:
                    by_rule_method[(rule.rule, m)].add(str(rule.endpoint))

            collisions = [(k, v) for k, v in by_rule_method.items() if len(v) > 1]
            if collisions:
                parts = []
                for (rule, method), eps in collisions[:6]:
                    parts.append(f"{method} {rule} -> {', '.join(sorted(eps))}")
                self.add(
                    "F1_ROUTE_DUPLICATES",
                    "FAIL",
                    "동일 URL/메서드에 여러 엔드포인트가 매핑됐어요.",
                    detail=" | ".join(parts),
                    hint="중복 라우트를 정리해 URL 1개=구현 1군데 원칙으로 맞추세요.",
                )
            else:
                self.add("F1_ROUTE_DUPLICATES", "PASS", "동일 URL/메서드 충돌이 발견되지 않았어요.")
        except Exception as exc:
            self.add("F1_ROUTE_DUPLICATES", "FAIL", "라우트 중복 점검 실패", detail=self._short_err(exc))

    def run(self) -> int:
        self.check_app_boot_import()
        self.check_migrations()
        self.check_route_smoke()
        self.check_assets_quiz_regression_isolated()
        self.check_p0_static()
        self.check_security_basics()
        self.check_nhis_engine_sanity()
        self.check_route_duplicates()
        return self.print_report()

    def print_report(self) -> int:
        order = {"FAIL": 0, "WARN": 1, "SKIP": 2, "PASS": 3}
        self.results.sort(key=lambda r: (order.get(r.status, 9), r.key))
        counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
        for row in self.results:
            counts[row.status] = counts.get(row.status, 0) + 1

        print("=" * 78)
        print("SafeToSpend Self Audit Report")
        print("=" * 78)
        for row in self.results:
            print(f"[{row.status:4}] {row.key} - {row.summary}")
            if row.detail:
                print(f"       detail: {row.detail}")
            if row.hint:
                print(f"       hint  : {row.hint}")
        print("-" * 78)
        print(
            f"SUMMARY  PASS={counts['PASS']}  FAIL={counts['FAIL']}  WARN={counts['WARN']}  SKIP={counts['SKIP']}"
        )
        print("=" * 78)
        return 1 if counts["FAIL"] > 0 else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SafeToSpend 셀프 점검(부팅/마이그레이션/스모크/보안)")
    parser.add_argument(
        "--no-upgrade",
        action="store_true",
        help="db upgrade 실행을 건너뛰고 점검을 수행합니다.",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="DB 연결이 필요한 점검(db heads/upgrade, 로그인 스모크)을 건너뜁니다.",
    )
    parser.add_argument(
        "--strict-db",
        action="store_true",
        help="DB 연결 실패를 SKIP으로 처리하지 않고 FAIL로 처리합니다.",
    )
    args = parser.parse_args(argv)

    try:
        audit = SelfAudit(
            run_upgrade=(not args.no_upgrade),
            no_db=bool(args.no_db),
            strict_db=bool(args.strict_db),
        )
        return audit.run()
    except Exception:
        print("=" * 78)
        print("[FAIL] SELF_AUDIT_RUNTIME - 셀프 점검 스크립트 실행 중 예외가 발생했어요.")
        print("       detail:", traceback.format_exc(limit=2).strip().replace("\n", " | "))
        print("       hint  : scripts/self_audit.py 내부 예외 처리 경로를 확인하세요.")
        print("=" * 78)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
