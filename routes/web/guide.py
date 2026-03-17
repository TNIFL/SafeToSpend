from __future__ import annotations

from flask import Blueprint, render_template

from services.receipt_expense_guidance import get_receipt_expense_guidance_content


web_guide_bp = Blueprint("web_guide", __name__)


def get_official_data_guide_content() -> dict:
    sections = [
        {
            "id": "hometax",
            "title": "홈택스 자료",
            "summary": "세금·증빙 정확도를 올릴 때 자주 쓰는 공식 자료예요.",
            "items": [
                {
                    "easy_name": "현금영수증 지출증빙 내역",
                    "purpose": "누락된 지출증빙이 있는지 확인해서 비용 반영 정확도를 올려요.",
                    "shortcut_url": "https://www.hometax.go.kr/",
                    "shortcut_label": "홈택스 바로가기",
                    "help_url": "https://mob.hometax.go.kr/home/mtmhighmtmhigh100M01.do?metaTy=MANUAL",
                    "menu_path": "홈택스 > 전자(세금)계산서·현금영수증·신용카드 > 현금영수증(사업자) > 매입내역 조회",
                    "caution": "홈택스 화면이 바뀌면 '전자(세금)계산서·현금영수증·신용카드' 묶음 메뉴부터 찾아보세요.",
                    "storage_default": "기본은 필요한 추출값만 먼저 정리하고, 원본 저장은 선택 구조를 우선 검토해요.",
                },
                {
                    "easy_name": "사업용 카드 사용내역",
                    "purpose": "카드로 쓴 비용을 한 번에 맞춰 review 수작업을 줄여요.",
                    "upload_document_type": "hometax_business_card_usage",
                    "shortcut_url": "https://www.hometax.go.kr/",
                    "shortcut_label": "홈택스 바로가기",
                    "help_url": "https://call.nts.go.kr/call/qna/selectQnaInfo.do?mi=1311&ctgId=CTG11924&ctgSubId=CTGS12158",
                    "menu_path": "홈택스 > 전자(세금)계산서·현금영수증·신용카드 > 신용카드 > 사업용 신용카드 사용내역",
                    "caution": "사업용 카드 메뉴명은 개편에 따라 달라질 수 있어요. '신용카드' 묶음 메뉴를 먼저 보세요.",
                    "storage_default": "기본은 사용일·가맹점·금액 같은 핵심 추출값 우선 구조예요.",
                },
                {
                    "easy_name": "전자세금계산서 내역",
                    "purpose": "매입·매출 증빙을 정리하고 세무사 전달 자료를 맞추는 데 도움을 줘요.",
                    "shortcut_url": "https://www.hometax.go.kr/",
                    "shortcut_label": "홈택스 바로가기",
                    "help_url": "https://mob.hometax.go.kr/home/mtmhighmtmhigh100M01.do?metaTy=MANUAL",
                    "menu_path": "홈택스 > 조회/발급 > 전자세금계산서 > 발급/수취 내역 조회",
                    "caution": "발급·수취 구분이 나뉘어 있으면 둘 다 확인해야 할 수 있어요.",
                    "storage_default": "기본은 문서 종류·공급가액·작성일 같은 추출값 우선이에요.",
                },
                {
                    "easy_name": "이미 빠진 세금/지급명세 자료",
                    "purpose": "돈 받을 때 이미 빠진 세금과 지급명세를 맞춰 세금 정확도를 올려요.",
                    "upload_document_type": "hometax_withholding_statement",
                    "shortcut_url": "https://www.hometax.go.kr/",
                    "shortcut_label": "홈택스 바로가기",
                    "help_url": "https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7665&mi=2225",
                    "menu_path": "홈택스 > My홈택스 또는 조회/발급 > 지급명세서 / 원천징수 / 납부내역",
                    "caution": "메뉴가 안 보이면 My홈택스와 조회/발급을 먼저 확인해 보세요.",
                    "storage_default": "기본은 기간·세액·지급처 같은 추출값 우선 구조예요.",
                },
            ],
        },
        {
            "id": "nhis",
            "title": "NHIS 자료",
            "summary": "건보료 기준일과 가입상태를 더 정확히 맞출 때 쓰는 공식 자료예요.",
            "items": [
                {
                    "easy_name": "건보료 납부확인서",
                    "purpose": "건보료 추정과 실제 납부 흐름 차이를 줄이는 데 써요.",
                    "upload_document_type": "nhis_payment_confirmation",
                    "shortcut_url": "https://www.nhis.or.kr/",
                    "shortcut_label": "NHIS 바로가기",
                    "help_url": "https://www.nhis.or.kr/nhis/minwon/minwonGuide.do?mode=view&articleNo=146671",
                    "menu_path": "국민건강보험 > 민원서비스 > 서비스찾기 > 보험료 납부확인서",
                    "caution": "민원서비스 화면이 바뀌면 '서비스찾기' 또는 '증명서 발급/확인' 메뉴를 먼저 보세요.",
                    "storage_default": "민감 가능 정보가 있어 기본은 핵심 추출값 저장, 원본은 기본 비저장 방향이에요.",
                },
                {
                    "easy_name": "건보 자격 변동 확인서",
                    "purpose": "직장가입자·지역가입자·피부양자 전환 시점을 설명하는 데 써요.",
                    "shortcut_url": "https://www.nhis.or.kr/",
                    "shortcut_label": "NHIS 바로가기",
                    "help_url": "https://www.nhis.or.kr/nhis/minwon/minwonGuide.do?mode=view&articleNo=146671",
                    "menu_path": "국민건강보험 > 민원서비스 > 서비스찾기 > 자격득실확인서",
                    "caution": "가입유형과 자격 변동 시점처럼 필요한 값만 먼저 반영하는 방향으로 안내해요.",
                    "storage_default": "기본은 자격 변동 시점과 상태값 같은 추출값 우선 구조예요.",
                },
            ],
        },
    ]
    sources = [
        {
            "title": "국세청 종합소득세 신고납부기한 안내",
            "url": "https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7665&mi=2225",
            "note": "종합소득세 신고·납부 기한과 시즌 안내 기준을 확인할 때 쓰는 공식 자료예요.",
        },
        {
            "title": "홈택스 공식 사이트",
            "url": "https://www.hometax.go.kr/",
            "note": "현금영수증, 사업용 카드, 전자세금계산서, 지급명세 자료를 찾는 기본 출발점이에요.",
        },
        {
            "title": "NHIS 민원서비스 안내",
            "url": "https://www.nhis.or.kr/nhis/minwon/minwonGuide.do?mode=view&articleNo=146671",
            "note": "보험료 납부확인서와 자격득실확인서 발급 안내를 찾을 때 쓰는 공식 페이지예요.",
        },
        {
            "title": "개인정보보호법",
            "url": "https://www.law.go.kr/법령/개인정보보호법",
            "note": "수집·이용, 최소수집, 보유기간, 파기 원칙을 설계할 때 기준이 되는 법령이에요.",
        },
        {
            "title": "개인정보의 안전성 확보조치 기준",
            "url": "https://www.law.go.kr/행정규칙/개인정보의안전성확보조치기준",
            "note": "공식 자료를 저장할 때 필요한 접근통제와 안전조치 기준을 볼 때 쓰는 공식 기준이에요.",
        },
    ]
    return {
        "title": "공식 자료 가져오기 안내",
        "summary": "홈택스와 NHIS에서 어디로 들어가야 하는지, 자료를 올리면 어떤 숫자가 좋아지는지 한 번에 정리했어요.",
        "sections": sections,
        "sources": sources,
    }


@web_guide_bp.get("/guide/expense")
def expense_guide():
    payload = get_receipt_expense_guidance_content()
    return render_template("guide/expense-guide.html", **payload)


@web_guide_bp.get("/guide/official-data")
def official_data_guide():
    payload = get_official_data_guide_content()
    return render_template("guide/official-data-guide.html", **payload)
