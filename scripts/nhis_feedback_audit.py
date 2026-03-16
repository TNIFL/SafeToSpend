from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

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


class NhisFeedbackAudit:
    def __init__(self) -> None:
        self.results: list[CheckResult] = []
        self.app = None
        self.client = None

    def add(self, key: str, status: str, summary: str, detail: str = "", hint: str = "") -> None:
        self.results.append(CheckResult(key=key, status=status, summary=summary, detail=detail, hint=hint))

    def _short_err(self, exc: BaseException) -> str:
        return f"{type(exc).__name__}: {exc}"

    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8", errors="ignore")

    def _db_unavailable(self, text: str) -> bool:
        t = (text or "").lower()
        return any(
            token in t
            for token in (
                "operationalerror",
                "connection refused",
                "could not connect",
                "operation not permitted",
                "connection to server",
            )
        )

    def _extract_label_month(self, html: str, prefix: str) -> str:
        pat = re.compile(rf"{re.escape(prefix)}\((\d{{4}}-\d{{2}})\)")
        m = pat.search(html)
        return m.group(1) if m else ""

    def check_boot(self) -> None:
        try:
            from app import create_app

            self.app = create_app()
            self.app.config.update(TESTING=True)
            self.client = self.app.test_client()
            self.add("A1_APP_BOOT", "PASS", "앱 import/create_app 성공")
        except Exception as exc:
            self.add(
                "A1_APP_BOOT",
                "FAIL",
                "앱 import/create_app 실패",
                detail=self._short_err(exc),
                hint="app.py 초기화/블루프린트 import를 확인하세요.",
            )

    def check_assets_routes_anon(self) -> None:
        if self.client is None:
            self.add("B1_ASSETS_ANON", "SKIP", "앱 생성 실패로 건너뛰었어요.")
            return

        paths = [
            "/dashboard/assets?month=2026-03&skip_quiz=1",
            "/dashboard/assets?month=2026-10&skip_quiz=1",
            "/dashboard/assets?month=2026-11&skip_quiz=1",
        ]
        parts: list[str] = []
        failed = False
        for path in paths:
            try:
                res = self.client.get(path, follow_redirects=False)
                code = int(res.status_code)
                parts.append(f"{path}:{code}")
                if code >= 500:
                    failed = True
            except Exception as exc:
                parts.append(f"{path}:ERR({type(exc).__name__})")
                failed = True

        if failed:
            self.add(
                "B1_ASSETS_ANON",
                "FAIL",
                "비로그인 assets 스모크에서 500/예외가 발생했어요.",
                detail=" | ".join(parts),
                hint="assets 라우트의 권한 분기/렌더 예외를 확인하세요.",
            )
        else:
            self.add("B1_ASSETS_ANON", "PASS", "비로그인 assets 스모크 통과", detail=" | ".join(parts))

    def check_template_contract(self) -> None:
        try:
            html = self._read("templates/assets.html")
            required = [
                "현재 적용(",
                "11월 반영(",
                "차이 (추정)",
                "건강보험료(추정)",
                "장기요양(추정)",
                "합계(추정)",
                "근거 보기 (추정)",
                "건보료는 매월 납부해요",
                "다음 달 10일까지",
                "11월은 ‘내는 달’이 아니라",
            ]
            missing = [token for token in required if token not in html]
            if missing:
                self.add(
                    "B2_TEMPLATE_CONTRACT",
                    "FAIL",
                    "assets 템플릿 필수 라벨/문구가 누락됐어요.",
                    detail=f"missing={missing}",
                    hint="templates/assets.html 즉시 피드백 섹션 라벨을 확인하세요.",
                )
                return

            debug_required = [
                "개발용 분해 보기",
                "nov_calc_reused_current",
                "fallback_used",
                "fallback_reason",
                "income_points",
                "property_points",
            ]
            debug_missing = [token for token in debug_required if token not in html]
            if debug_missing:
                self.add(
                    "B2_TEMPLATE_CONTRACT",
                    "WARN",
                    "assets 템플릿 디버그 분해 라벨이 일부 누락됐어요.",
                    detail=f"debug_missing={debug_missing}",
                    hint="templates/assets.html debug_nhis 섹션을 확인하세요.",
                )
            else:
                self.add("B2_TEMPLATE_CONTRACT", "PASS", "assets 템플릿 필수 라벨/디버그 라벨 계약 통과")
        except Exception as exc:
            self.add(
                "B2_TEMPLATE_CONTRACT",
                "FAIL",
                "assets 템플릿 계약 점검 중 예외가 발생했어요.",
                detail=self._short_err(exc),
            )

    def _find_login_user_id(self) -> tuple[int | None, bool]:
        """returns (user_id, can_debug)"""
        if self.app is None:
            return None, False
        try:
            with self.app.app_context():
                from domain.models import User

                user = User.query.filter(User.is_admin.is_(True)).order_by(User.id.asc()).first()
                can_debug = bool(user)

                if not user:
                    user = User.query.filter_by(email="test+local@safetospend.local").first()
                    can_debug = bool(self.app.debug)

                if not user:
                    user = User.query.order_by(User.id.asc()).first()
                    can_debug = bool(self.app.debug)

                return (int(user.id), can_debug) if user else (None, False)
        except Exception:
            return None, False

    def check_assets_labels_auth(self) -> None:
        if self.client is None or self.app is None:
            self.add("C1_ASSETS_LABELS", "SKIP", "앱 생성 실패로 건너뛰었어요.")
            return

        try:
            user_id, can_debug = self._find_login_user_id()
            if not user_id:
                self.add("C1_ASSETS_LABELS", "SKIP", "로그인 가능한 사용자 계정을 찾지 못해 건너뛰었어요.")
                return

            with self.client.session_transaction() as sess:
                sess["user_id"] = int(user_id)
                sess.permanent = True

            month_cases = ["2026-03", "2026-10", "2026-11"]
            failures: list[str] = []
            details: list[str] = []

            for month in month_cases:
                path = f"/dashboard/assets?month={month}&skip_quiz=1"
                res = self.client.get(path, follow_redirects=True)
                code = int(res.status_code)
                details.append(f"{path}:{code}")
                if code >= 500:
                    failures.append(f"{month}:status_{code}")
                    continue
                html = res.get_data(as_text=True)

                required_tokens = [
                    f"현재 적용({month})",
                    f"11월 반영({month[:4]}-11)",
                    "차이 (추정)",
                    "건강보험료(추정)",
                    "장기요양(추정)",
                    "합계(추정)",
                ]
                missing = [t for t in required_tokens if t not in html]
                if missing:
                    failures.append(f"{month}:missing={','.join(missing)}")

                label_month = self._extract_label_month(html, "현재 적용")
                label_nov = self._extract_label_month(html, "11월 반영")
                if label_month != month:
                    failures.append(f"{month}:label_current={label_month or '-'}")
                if label_nov != f"{month[:4]}-11":
                    failures.append(f"{month}:label_nov={label_nov or '-'}")

            if failures:
                self.add(
                    "C1_ASSETS_LABELS",
                    "FAIL",
                    "assets 즉시 피드백 라벨/문구 점검 실패",
                    detail=" | ".join(details + failures),
                    hint="templates/assets.html의 라벨 및 month 반영 값을 확인하세요.",
                )
            else:
                self.add(
                    "C1_ASSETS_LABELS",
                    "PASS",
                    "assets 즉시 피드백 라벨/문구 점검 통과",
                    detail=" | ".join(details),
                )

            debug_path = "/dashboard/assets?month=2026-03&skip_quiz=1&debug_nhis=1"
            debug_res = self.client.get(debug_path, follow_redirects=True)
            debug_html = debug_res.get_data(as_text=True) if int(debug_res.status_code) < 500 else ""

            if int(debug_res.status_code) >= 500:
                self.add(
                    "C2_ASSETS_DEBUG",
                    "FAIL",
                    "debug_nhis 페이지 응답 실패",
                    detail=f"status={debug_res.status_code}",
                )
            else:
                debug_markers = [
                    "개발용 분해 보기",
                    "nov_calc_reused_current",
                    "fallback_used",
                    "income_points",
                    "property_points",
                ]
                has_debug = all(m in debug_html for m in debug_markers)
                if can_debug:
                    if has_debug:
                        self.add("C2_ASSETS_DEBUG", "PASS", "관리자/개발자 debug_nhis 분해 출력 확인")
                    else:
                        self.add(
                            "C2_ASSETS_DEBUG",
                            "FAIL",
                            "관리자/개발자 debug_nhis 분해 출력이 누락됐어요.",
                            hint="assets 템플릿 debug 섹션 조건과 payload 전달을 확인하세요.",
                        )
                else:
                    if has_debug:
                        self.add(
                            "C2_ASSETS_DEBUG",
                            "WARN",
                            "일반 사용자에게 debug_nhis 분해가 노출됐어요.",
                            hint="관리자/DEBUG 조건 게이트를 확인하세요.",
                        )
                    else:
                        self.add("C2_ASSETS_DEBUG", "PASS", "일반 사용자 debug_nhis 분해 비노출 확인")

        except Exception as exc:
            msg = self._short_err(exc)
            if self._db_unavailable(msg):
                self.add("C1_ASSETS_LABELS", "SKIP", "DB 연결 제약으로 로그인 점검을 건너뛰었어요.", detail=msg)
                self.add("C2_ASSETS_DEBUG", "SKIP", "DB 연결 제약으로 debug 점검을 건너뛰었어요.", detail=msg)
            else:
                self.add(
                    "C1_ASSETS_LABELS",
                    "FAIL",
                    "assets 로그인 점검 중 예외가 발생했어요.",
                    detail=msg,
                )

    def run(self) -> int:
        self.check_boot()
        self.check_assets_routes_anon()
        self.check_template_contract()
        self.check_assets_labels_auth()
        return self.print_report()

    def print_report(self) -> int:
        order = {"FAIL": 0, "WARN": 1, "SKIP": 2, "PASS": 3}
        self.results.sort(key=lambda r: (order.get(r.status, 9), r.key))

        counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
        for row in self.results:
            counts[row.status] = counts.get(row.status, 0) + 1
            print(f"[{row.status}] {row.key} - {row.summary}")
            if row.detail:
                print(f"  detail: {row.detail}")
            if row.hint:
                print(f"  hint: {row.hint}")

        print("\n== NHIS FEEDBACK AUDIT SUMMARY ==")
        print(
            f"PASS={counts['PASS']} FAIL={counts['FAIL']} "
            f"WARN={counts['WARN']} SKIP={counts['SKIP']}"
        )
        return 1 if counts["FAIL"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(NhisFeedbackAudit().run())
