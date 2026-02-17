# services/tasks.py
"""services/tasks.py

'이번 주 할 일 1개'는 리텐션 장치다.

- 사용자에게는 '이번 주에 딱 1개만 하면 되는 것'만 보여준다.
- 내부적으로는 리스크 우선순위를 기준으로 자동 생성/갱신한다.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from core.extensions import db
from core.time import utcnow
from domain.models import WeeklyTask
from services.risk import compute_risk_summary

KST = ZoneInfo("Asia/Seoul")


def _week_key_kst(now: datetime | None = None) -> str:
    now = now or datetime.now(KST)
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def refresh_weekly_task(user_pk: int) -> WeeklyTask:
    wk = _week_key_kst()
    task = WeeklyTask.query.filter_by(user_pk=user_pk, week_key=wk).first()
    if task and task.is_done:
        return task

    r = compute_risk_summary(user_pk)

    if r.evidence_missing_required > 0:
        title = f"이번 주 할 일 1개: 증빙 {min(3, r.evidence_missing_required)}개만 처리하기"
        kind = "evidence_required"
        cta_label = "증빙 처리"
        cta_url = "/inbox?tab=evidence"
    elif r.expense_needs_review > 0:
        title = f"이번 주 할 일 1개: 개인/업무 {min(5, r.expense_needs_review)}건만 분류하기"
        kind = "expense_label"
        cta_label = "분류하기"
        cta_url = "/inbox?tab=mixed"
    elif r.buffer_shortage_krw > 0:
        title = f"이번 주 할 일 1개: 세금 금고 {r.buffer_shortage_krw:,}원 채우기"
        kind = "buffer_topup"
        cta_label = "확인하기"
        cta_url = "/dashboard/tax-buffer"
    else:
        title = "이번 주 할 일 1개: 이번 주 거래 1번만 업로드하기"
        kind = "import"
        cta_label = "CSV 업로드"
        cta_url = "/inbox/import"

    if not task:
        task = WeeklyTask(
            user_pk=user_pk,
            week_key=wk,
            title=title,
            kind=kind,
            cta_label=cta_label,
            cta_url=cta_url,
            meta={
                "risk": {
                    "evidence_required": r.evidence_missing_required,
                    "expense_review": r.expense_needs_review,
                    "buffer_shortage": r.buffer_shortage_krw,
                }
            },
        )
        db.session.add(task)
    else:
        task.title = title
        task.kind = kind
        task.cta_label = cta_label
        task.cta_url = cta_url
        task.meta = {
            "risk": {
                "evidence_required": r.evidence_missing_required,
                "expense_review": r.expense_needs_review,
                "buffer_shortage": r.buffer_shortage_krw,
            }
        }

    db.session.commit()
    return task


def mark_weekly_task_done(user_pk: int) -> None:
    wk = _week_key_kst()
    task = WeeklyTask.query.filter_by(user_pk=user_pk, week_key=wk).first()
    if not task:
        return
    task.is_done = True
    task.done_at = utcnow()
    db.session.commit()
