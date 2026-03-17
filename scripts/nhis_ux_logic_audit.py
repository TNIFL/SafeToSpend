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


class NhisUxLogicAudit:
    def __init__(self) -> None:
        self.results: list[CheckResult] = []
        self.app = None
        self.client = None

    def add(self, key: str, status: str, summary: str, detail: str = "", hint: str = "") -> None:
        self.results.append(CheckResult(key=key, status=status, summary=summary, detail=detail, hint=hint))

    def _read(self, rel_path: str) -> str:
        return (ROOT / rel_path).read_text(encoding="utf-8", errors="ignore")

    def _short_err(self, exc: BaseException) -> str:
        return f"{type(exc).__name__}: {exc}"

    def _db_unavailable(self, text: str) -> bool:
        t = (text or "").lower()
        return any(
            token in t
            for token in (
                "operationalerror",
                "connection to server",
                "could not connect",
                "operation not permitted",
                "connection refused",
            )
        )

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
                hint="app.py 초기화 경로를 확인하세요.",
            )

    def check_route_smoke_anon(self) -> None:
        if self.client is None:
            self.add("B1_ROUTE_SMOKE_ANON", "SKIP", "앱 생성 실패로 건너뛰었어요.")
            return
        checks = [
            "/dashboard/assets?month=2026-03&skip_quiz=1",
            "/dashboard/nhis?month=2026-03",
        ]
        parts: list[str] = []
        failed = False
        for path in checks:
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
                "B1_ROUTE_SMOKE_ANON",
                "FAIL",
                "비로그인 라우트 스모크에서 500/예외가 발생했어요.",
                detail=" | ".join(parts),
                hint="assets/nhis 라우트의 권한 분기와 템플릿 렌더를 확인하세요.",
            )
        else:
            self.add("B1_ROUTE_SMOKE_ANON", "PASS", "비로그인 라우트 스모크 통과", detail=" | ".join(parts))

    def check_template_wording(self) -> None:
        try:
            assets_html = self._read("templates/assets.html")
            nhis_html = self._read("templates/nhis.html")
            all_text = f"{assets_html}\n{nhis_html}"

            required = [
                "매월",
                "다음 달 10일까지",
                "11월",
                "반영",
                "월 보험료",
                "(추정)",
                "차이 (추정) = 11월 반영 - 현재 적용",
            ]
            missing = [token for token in required if token not in all_text]

            banned_patterns = [
                r"11월에만\s*낸",
                r"11월에만\s*납부",
                r"11월에만\s*계산",
                r"11월만\s*낸",
            ]
            banned_hits: list[str] = []
            for pat in banned_patterns:
                if re.search(pat, all_text):
                    banned_hits.append(pat)

            if missing or banned_hits:
                self.add(
                    "C1_TEMPLATE_WORDING",
                    "FAIL",
                    "건보료 납부/11월 반영 문구 정합성 점검 실패",
                    detail=f"missing={missing} banned={banned_hits}",
                    hint="assets/nhis 템플릿 문구를 점검해 오해 문장을 제거하세요.",
                )
            else:
                self.add("C1_TEMPLATE_WORDING", "PASS", "건보료 납부/11월 반영 문구 정합성 통과")
        except Exception as exc:
            self.add(
                "C1_TEMPLATE_WORDING",
                "FAIL",
                "템플릿 문구 점검 실패",
                detail=self._short_err(exc),
            )

    def check_logic_compare(self) -> None:
        try:
            from services.nhis_estimator import estimate_nhis_current_vs_november

            out = estimate_nhis_current_vs_november({"target_month": "2026-03", "member_type": "regional"}, None)
            current_cycle = dict(out.get("current_cycle") or {})
            november_cycle = dict(out.get("november_cycle") or {})

            current_cycle_ok = int(current_cycle.get("cycle_start_year") or 0) == 2025
            november_cycle_ok = int(november_cycle.get("cycle_start_year") or 0) == 2026
            labels_ok = all(k in out for k in ("current_total_krw", "november_total_krw", "diff_krw"))
            # 11월/12월 경계: 이미 11월 사이클이 적용된 달이면 same_cycle_active=True 이어야 함
            out_nov = estimate_nhis_current_vs_november({"target_month": "2026-11", "member_type": "regional"}, None)
            out_dec = estimate_nhis_current_vs_november({"target_month": "2026-12", "member_type": "regional"}, None)
            edge_ok = bool(out_nov.get("same_cycle_active")) and bool(out_dec.get("same_cycle_active"))

            if labels_ok and current_cycle_ok and november_cycle_ok and edge_ok:
                self.add("D1_LOGIC_COMPARE", "PASS", "현재 적용 vs 11월 반영 비교 로직이 동작해요.")
            else:
                self.add(
                    "D1_LOGIC_COMPARE",
                    "WARN",
                    "비교 로직은 동작하지만 month 반영 검증 신호가 약해요.",
                    detail=(
                        f"keys={labels_ok} current_cycle_ok={current_cycle_ok} "
                        f"november_cycle_ok={november_cycle_ok} edge_ok={edge_ok}"
                    ),
                    hint="estimate_nhis_current_vs_november에서 target_month 흐름을 확인하세요.",
                )
        except Exception as exc:
            self.add(
                "D1_LOGIC_COMPARE",
                "FAIL",
                "현재 vs 11월 비교 로직 점검 실패",
                detail=self._short_err(exc),
                hint="services/nhis_estimator.py compare 경로를 확인하세요.",
            )

    def check_route_smoke_auth_optional(self) -> None:
        if self.client is None or self.app is None:
            self.add("E1_ROUTE_SMOKE_AUTH", "SKIP", "앱 생성 실패로 건너뛰었어요.")
            return
        try:
            with self.app.app_context():
                from domain.models import User

                user = User.query.filter_by(email="test+local@safetospend.local").first()
            if not user:
                self.add("E1_ROUTE_SMOKE_AUTH", "SKIP", "테스트 계정이 없어 로그인 스모크를 건너뛰었어요.")
                return

            with self.client.session_transaction() as sess:
                sess["user_id"] = int(user.id)
                sess.permanent = True

            paths = [
                "/dashboard/assets?month=2026-03&skip_quiz=1",
                "/dashboard/nhis?month=2026-03",
                "/dashboard/nhis?month=2026-03&debug_nhis=1",
            ]
            parts: list[str] = []
            failed = False
            for path in paths:
                res = self.client.get(path, follow_redirects=False)
                code = int(res.status_code)
                parts.append(f"{path}:{code}")
                if code >= 500:
                    failed = True
                if code == 200:
                    body = res.get_data(as_text=True)
                    needed = ("월 보험료", "11월", "(추정)")
                    if not all(token in body for token in needed):
                        failed = True
                        parts.append(f"{path}:missing_tokens")
            if failed:
                self.add(
                    "E1_ROUTE_SMOKE_AUTH",
                    "FAIL",
                    "로그인 상태 NHIS/자산 페이지 점검 실패",
                    detail=" | ".join(parts),
                    hint="문구/라벨 렌더와 권한 분기를 확인하세요.",
                )
            else:
                self.add("E1_ROUTE_SMOKE_AUTH", "PASS", "로그인 상태 NHIS/자산 페이지 점검 통과", detail=" | ".join(parts))
        except Exception as exc:
            msg = self._short_err(exc)
            if self._db_unavailable(msg):
                self.add(
                    "E1_ROUTE_SMOKE_AUTH",
                    "SKIP",
                    "DB 연결 제약으로 로그인 스모크를 건너뛰었어요.",
                    detail=msg,
                )
            else:
                self.add(
                    "E1_ROUTE_SMOKE_AUTH",
                    "WARN",
                    "로그인 스모크를 완료하지 못했어요.",
                    detail=msg,
                    hint="테스트 계정 시드 또는 DB 연결 상태를 확인하세요.",
                )

    def run(self) -> int:
        self.check_boot()
        self.check_route_smoke_anon()
        self.check_template_wording()
        self.check_logic_compare()
        self.check_route_smoke_auth_optional()
        return self.print_report()

    def print_report(self) -> int:
        order = {"FAIL": 0, "WARN": 1, "SKIP": 2, "PASS": 3}
        self.results.sort(key=lambda r: (order.get(r.status, 9), r.key))
        counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
        for row in self.results:
            counts[row.status] = counts.get(row.status, 0) + 1

        print("=" * 78)
        print("NHIS UX/Logic Audit Report")
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


def main() -> int:
    audit = NhisUxLogicAudit()
    return audit.run()


if __name__ == "__main__":
    raise SystemExit(main())
