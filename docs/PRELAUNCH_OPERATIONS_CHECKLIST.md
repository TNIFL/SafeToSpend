# Prelaunch Operations Checklist

작성일: 2026-03-11
목적: 오픈 직전 운영 최종 확인용 단일 체크리스트

## 1) 결제/구독
| 항목 | 확인 방법 | 완료 기준 |
|---|---|---|
| billing startup check | `FLASK_APP=app.py .venv/bin/flask billing-startup-check` | `ok` 출력 |
| 결제 복구 CLI 점검 | `billing-reconcile`, `billing-replay-event`, `billing-reproject-entitlement` 실행 기록 확인 | 최소 2개 이상 실동작 기록 |
| 오염 데이터 점검 | `scripts/billing_data_audit.py --limit 300` | 치명 오염 0건 또는 수동조치 계획 존재 |

## 2) 백업/복구
| 항목 | 확인 방법 | 완료 기준 |
|---|---|---|
| DB 백업 리허설 | `docs/DB_BACKUP_REHEARSAL_RESULTS.md` 확인 | 최소 1회 성공 기록 |
| DB 복구 리허설 | `docs/DB_RESTORE_REHEARSAL_RESULTS.md` 확인 | 최소 1회 성공/실패 원인 기록 |
| 파일 백업/복구 리허설 | `docs/FILE_BACKUP_RECOVERY_RESULTS.md` 확인 | 샘플 복구 검증 기록 |

## 3) 정책/문서
| 항목 | 확인 방법 | 완료 기준 |
|---|---|---|
| 이용약관 초안 | `docs/TERMS_OF_SERVICE_DRAFT.md` | 최신 정책 반영 |
| 개인정보처리방침 초안 | `docs/PRIVACY_POLICY_DRAFT.md` | 저장/미저장 데이터 구분 명확 |
| 결제/구독 정책 초안 | `docs/BILLING_AND_SUBSCRIPTION_POLICY_DRAFT.md` | 가격/해지/grace 정책 일치 |
| 환불/해지 정책 초안 | `docs/REFUND_AND_CANCELLATION_POLICY_DRAFT.md` | 환불없음/기간종료해지 일치 |
| 정책 공개 계획 | `docs/POLICY_PUBLICATION_PLAN.md` | 공개 경로/템플릿 계획 명시 |

## 4) 문의/공지
| 항목 | 확인 방법 | 완료 기준 |
|---|---|---|
| 문의 채널 | `docs/CUSTOMER_SUPPORT_MINIMUM.md` + `/support` 실동작 | 사용자 문의 경로 확인 가능 |
| 공지 구조 | `docs/STATUS_NOTICE_MINIMUM.md` | 장애/결제/지연 템플릿 준비 |

## 5) 스테이징/인프라 실측
| 항목 | 확인 방법 | 완료 기준 |
|---|---|---|
| 스테이징 E2E | `docs/BILLING_E2E_RESULTS_STAGING.md` | 핵심 시나리오 2개 이상 PASS |
| webhook/refresh/세션없음 | 같은 문서 | PASS 또는 명확한 FAIL 원인 |
| 프록시/APM/CSP | `docs/BILLING_INFRA_VALIDATION.md` | 민감값 비노출 실측 완료 |

## 6) 최종 게이트 규칙
- 정책 문서/백업복구/스테이징 실측/인프라 실측 중 하나라도 미완료면 실오픈 `NO-GO` 또는 `CONDITIONAL GO`.
- 실오픈 `GO`는 실측 근거(브라우저, DB 상태, 인프라 로그 확인)가 모두 충족될 때만 허용.
