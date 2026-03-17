from __future__ import annotations

import argparse

from app import create_app
from services.billing.service import cleanup_registration_attempts, normalize_registration_attempts_abandoned


def main() -> int:
    parser = argparse.ArgumentParser(description="billing registration attempt 정리")
    parser.add_argument("--abandoned-hours", type=int, default=2, help="미완료 started 상태를 abandoned 처리할 시간(시간)")
    parser.add_argument("--retention-days", type=int, default=90, help="failed/canceled 보관 기간(일)")
    parser.add_argument("--dry-run", action="store_true", help="실제 삭제 없이 대상 건수만 출력")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        normalized = normalize_registration_attempts_abandoned(
            abandoned_after_hours=max(1, int(args.abandoned_hours or 2))
        )
        result = cleanup_registration_attempts(
            retention_days=max(1, int(args.retention_days or 90)),
            dry_run=bool(args.dry_run),
        )

    print(
        "billing registration cleanup "
        f"normalized_abandoned={int(normalized)} "
        f"purged={int(result.get('purged_count') or 0)} "
        f"dry_run={bool(args.dry_run)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
