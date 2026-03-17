from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from openpyxl import load_workbook

from services.tax_package import PackageSnapshot, PackageStats, build_tax_package_zip_from_snapshot


class TaxPackageServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.evidence_path = Path(self.tmpdir.name) / "receipt.pdf"
        self.evidence_path.write_bytes(b"%PDF-1.4\nsample evidence\n")
        self.official_path = Path(self.tmpdir.name) / "hometax_payment.csv"
        self.official_path.write_text("조회일,2026-03-10\n최근 납부일,납부금액 합계,세목\n2026-03-09,150000,종합소득세\n", encoding="utf-8")

        stats = PackageStats(
            month_key="2026-03",
            period_start_kst="2026-03-01",
            period_end_kst="2026-03-31",
            generated_at_kst="2026-03-17 09:30",
            tx_total=2,
            tx_in_count=1,
            tx_out_count=1,
            sum_in_total=3200000,
            sum_out_total=180000,
            income_included_total=3200000,
            income_excluded_non_income_total=0,
            income_unknown_count=0,
            expense_business_total=180000,
            expense_personal_total=0,
            expense_mixed_total=0,
            expense_unknown_total=0,
            evidence_missing_required_count=0,
            evidence_missing_required_amount=0,
            evidence_missing_maybe_count=1,
            evidence_missing_maybe_amount=22000,
            evidence_attached_count=1,
            review_needed_count=1,
            tax_rate=0.15,
            tax_buffer_total=120000,
            tax_buffer_target=480000,
            tax_buffer_shortage=360000,
        )

        self.snapshot = PackageSnapshot(
            root_name="세무사전달패키지_2026-03_테스터",
            download_name="세무사전달패키지_2026-03_테스터.zip",
            display_name="테스터",
            stats=stats,
            transactions=[
                {
                    "tx_id": 101,
                    "occurred_at_kst": "2026-03-03 10:00",
                    "direction_label": "출금",
                    "amount_krw": 180000,
                    "counterparty": "카페 샘플",
                    "memo": "회의비",
                    "source_label": "자동연동",
                    "provider_label": "은행연동",
                    "classification_result_label": "업무지출",
                    "business_related_label": "예",
                    "evidence_status_label": "첨부됨",
                    "representative_evidence_type": "PDF 증빙",
                    "evidence_count": 1,
                    "evidence_zip_path": "증빙자료/101_receipt.pdf",
                    "trust_label": "반영됨",
                    "calculation_included_label": "예",
                    "recheck_required_label": "아니오",
                    "recheck_reason": "",
                    "evidence_note": "카드 전표 첨부",
                },
                {
                    "tx_id": 102,
                    "occurred_at_kst": "2026-03-05 16:20",
                    "direction_label": "출금",
                    "amount_krw": 22000,
                    "counterparty": "문구점",
                    "memo": "사무용품",
                    "source_label": "수동입력",
                    "provider_label": "없음",
                    "classification_result_label": "미확정",
                    "business_related_label": "미확정",
                    "evidence_status_label": "확인 필요",
                    "representative_evidence_type": "",
                    "evidence_count": 0,
                    "evidence_zip_path": "",
                    "trust_label": "재확인필요",
                    "calculation_included_label": "보류",
                    "recheck_required_label": "예",
                    "recheck_reason": "지출 분류가 아직 확정되지 않았습니다",
                    "evidence_note": "",
                },
            ],
            evidences=[
                {
                    "증빙번호": 9001,
                    "연결거래번호": 101,
                    "거래일시": "2026-03-03 10:00",
                    "거래처": "카페 샘플",
                    "금액": 180000,
                    "증빙종류": "PDF 증빙",
                    "파일명": "receipt.pdf",
                    "파일열기": ("열기", "증빙자료/101_receipt.pdf"),
                    "저장위치": "증빙자료/101_receipt.pdf",
                    "업로드일시": "2026-03-03 10:10",
                    "신뢰구분": "반영됨",
                    "계산반영여부": "예",
                    "재확인필요여부": "아니오",
                    "메모": "카드 전표 첨부",
                    "_zip_path": "증빙자료/101_receipt.pdf",
                    "_abs_path": self.evidence_path,
                }
            ],
            review_items=[
                {
                    "항목번호": 1,
                    "항목유형": "거래검토",
                    "관련자료구분": "거래내역",
                    "관련번호": 102,
                    "요약설명": "지출 분류가 아직 확정되지 않았습니다",
                    "현재상태": "미확정",
                    "필요한확인내용": "업무/개인/혼합 중 하나로 확정해 주세요",
                    "우선순위": "보통",
                    "메모": "사무용품",
                }
            ],
            evidence_missing_items=[
                {
                    "거래번호": 102,
                    "거래일시": "2026-03-05 16:20",
                    "거래처": "문구점",
                    "금액": 22000,
                    "증빙상태": "확인 필요",
                    "필요한확인내용": "업무 관련이면 증빙을 첨부하고, 아니면 불필요로 표시해 주세요",
                    "우선순위": "보통",
                }
            ],
            review_trade_items=[
                {
                    "거래번호": 102,
                    "거래일시": "2026-03-05 16:20",
                    "자료출처": "수동입력",
                    "거래처": "문구점",
                    "금액": 22000,
                    "현재상태": "미확정",
                    "재확인사유": "업무/개인 판단이 확정되지 않았습니다",
                    "필요한확인내용": "업무/개인/혼합 중 하나로 확정해 주세요",
                }
            ],
            included_source_labels=["수동입력", "자동연동"],
        )

        official_stats = replace(
            stats,
            official_data_total=1,
            official_data_parsed_count=1,
            official_data_review_count=0,
            official_data_unsupported_count=0,
            official_data_failed_count=0,
        )
        self.snapshot_with_official = PackageSnapshot(
            root_name="세무사전달패키지_2026-03_테스터",
            download_name="세무사전달패키지_2026-03_테스터.zip",
            display_name="테스터",
            stats=official_stats,
            transactions=self.snapshot.transactions,
            evidences=self.snapshot.evidences,
            review_items=self.snapshot.review_items,
            evidence_missing_items=self.snapshot.evidence_missing_items,
            review_trade_items=self.snapshot.review_trade_items,
            included_source_labels=self.snapshot.included_source_labels,
            official_documents=[
                {
                    "자료번호": 7001,
                    "기관명": "국세청(홈택스)",
                    "문서종류": "홈택스 납부내역",
                    "기준일": "2026-03-10",
                    "원본파일명": "hometax_payment.csv",
                    "파일열기": ("열기", "공식자료/홈택스_납부내역_2026-03-10.csv"),
                    "읽기상태": "반영 가능",
                    "검증상태": "검증 미실시",
                    "구조확인": "구조 확인됨",
                    "신뢰등급": "구조 확인됨 (B)",
                    "핵심값요약": "기준일: 2026-03-10 / 납부세액 합계: 150,000원",
                    "패키지반영여부": "예",
                    "재확인필요여부": "예",
                    "메모": "검증 미실시",
                    "_zip_path": "공식자료/홈택스_납부내역_2026-03-10.csv",
                    "_abs_path": self.official_path,
                    "_summary_items": [
                        {"label": "기준일", "value": "2026-03-10"},
                        {"label": "납부세액 합계", "value": "150,000원"},
                    ],
                }
            ],
        )

    def _build_zip(self, snapshot: PackageSnapshot | None = None) -> tuple[bytes, zipfile.ZipFile]:
        zip_io, filename = build_tax_package_zip_from_snapshot(snapshot or self.snapshot)
        self.assertEqual(filename, "세무사전달패키지_2026-03_테스터.zip")
        payload = zip_io.getvalue()
        archive = zipfile.ZipFile(io.BytesIO(payload))
        self.addCleanup(archive.close)
        return payload, archive

    def test_zip_contains_expected_files_and_keeps_official_outputs_when_no_docs(self) -> None:
        _, archive = self._build_zip()
        names = set(archive.namelist())
        root = "세무사전달패키지_2026-03_테스터"

        self.assertIn(f"{root}/00_패키지안내.txt", names)
        self.assertIn(f"{root}/01_패키지요약.xlsx", names)
        self.assertIn(f"{root}/02_거래정리.xlsx", names)
        self.assertIn(f"{root}/03_증빙목록.xlsx", names)
        self.assertIn(f"{root}/04_공식자료목록.xlsx", names)
        self.assertIn(f"{root}/05_확인필요항목.xlsx", names)
        self.assertIn(f"{root}/증빙자료/101_receipt.pdf", names)
        self.assertIn(f"{root}/공식자료/", names)

        self.assertFalse(any(name.startswith(f"{root}/참고자료/") for name in names))
        self.assertFalse(any(name.startswith(f"{root}/추가설명/") for name in names))

        official_wb = load_workbook(io.BytesIO(archive.read(f"{root}/04_공식자료목록.xlsx")))
        official_ws = official_wb["공식자료목록"]
        self.assertEqual(official_ws["C2"].value, "현재 포함된 공식자료 없음")

    def test_package_guide_explains_scope_and_limitations(self) -> None:
        _, archive = self._build_zip()
        guide = archive.read("세무사전달패키지_2026-03_테스터/00_패키지안내.txt").decode("utf-8")

        self.assertIn("04_공식자료목록.xlsx", guide)
        self.assertIn("공식자료/ : 대상 월에 포함된 공식자료 원본 파일", guide)
        self.assertIn("참고자료 폴더", guide)
        self.assertIn("거래당 대표 증빙 1개 기준", guide)
        self.assertIn("검증상태가 '검증 미실시'", guide)

    def test_workbooks_include_relative_evidence_hyperlinks(self) -> None:
        _, archive = self._build_zip()

        tx_wb = load_workbook(io.BytesIO(archive.read("세무사전달패키지_2026-03_테스터/02_거래정리.xlsx")))
        tx_ws = tx_wb["거래정리"]
        tx_headers = {cell.value: idx + 1 for idx, cell in enumerate(tx_ws[1])}
        tx_link_cell = tx_ws.cell(2, tx_headers["증빙바로열기"])
        self.assertEqual(tx_link_cell.value, "열기")
        self.assertIsNotNone(tx_link_cell.hyperlink)
        self.assertEqual(tx_link_cell.hyperlink.target, "증빙자료/101_receipt.pdf")

        evidence_wb = load_workbook(io.BytesIO(archive.read("세무사전달패키지_2026-03_테스터/03_증빙목록.xlsx")))
        evidence_ws = evidence_wb["증빙목록"]
        evidence_headers = {cell.value: idx + 1 for idx, cell in enumerate(evidence_ws[1])}
        evidence_link_cell = evidence_ws.cell(2, evidence_headers["파일열기"])
        self.assertEqual(evidence_link_cell.value, "열기")
        self.assertIsNotNone(evidence_link_cell.hyperlink)
        self.assertEqual(evidence_link_cell.hyperlink.target, "증빙자료/101_receipt.pdf")

    def test_official_data_workbook_and_folder_are_created_when_docs_exist(self) -> None:
        _, archive = self._build_zip(self.snapshot_with_official)
        names = set(archive.namelist())
        root = "세무사전달패키지_2026-03_테스터"

        self.assertIn(f"{root}/04_공식자료목록.xlsx", names)
        self.assertIn(f"{root}/공식자료/홈택스_납부내역_2026-03-10.csv", names)

        official_wb = load_workbook(io.BytesIO(archive.read(f"{root}/04_공식자료목록.xlsx")))
        official_ws = official_wb["공식자료목록"]
        headers = {cell.value: idx + 1 for idx, cell in enumerate(official_ws[1])}
        link_cell = official_ws.cell(2, headers["파일열기"])
        self.assertEqual(link_cell.value, "열기")
        self.assertIsNotNone(link_cell.hyperlink)
        self.assertEqual(link_cell.hyperlink.target, "공식자료/홈택스_납부내역_2026-03-10.csv")

        summary_ws = official_wb["공식자료반영요약"]
        self.assertEqual(summary_ws["A2"].value, "홈택스 납부내역")
        self.assertEqual(summary_ws["C2"].value, 1)

        key_ws = official_wb["공식자료핵심값"]
        self.assertEqual(key_ws["A2"].value, 7001)
        self.assertEqual(key_ws["D2"].value, "기준일")
