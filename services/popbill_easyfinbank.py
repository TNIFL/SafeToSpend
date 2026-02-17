# services/popbill_easyfinbank.py
from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from pathlib import Path

try:
    from popbill import EasyFinBankService
except Exception:  # pragma: no cover
    EasyFinBankService = None  # type: ignore


@dataclass(frozen=True)
class PopbillConfig:
    link_id: str
    secret_key: str
    corp_num: str
    user_id: str
    is_test: bool


class PopbillConfigError(RuntimeError):
    pass


class PopbillApiError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


_service: EasyFinBankService | None = None
_config: PopbillConfig | None = None
_ssl_ready: bool = False


def _ensure_https_ca_bundle() -> None:
    """
    macOS에서 흔히 발생하는 SSL CERTIFICATE_VERIFY_FAILED 대응.
    - certifi CA 번들을 사용하도록 강제해서, popbill SDK 내부가 urllib/requests 무엇을 쓰든
      최대한 인증서 검증이 통과되도록 만든다.
    """
    global _ssl_ready
    if _ssl_ready:
        return

    # 1) 사용자가 이미 지정한 CA 번들이 있으면 존중
    for env_key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        v = (os.getenv(env_key) or "").strip()
        if v and Path(v).exists():
            _ssl_ready = True
            return

    # 2) certifi CA 번들 우선 적용
    cafile = ""
    try:
        import certifi  # type: ignore

        cafile = certifi.where()
    except Exception:
        cafile = ""

    if cafile and Path(cafile).exists():
        os.environ.setdefault("SSL_CERT_FILE", cafile)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", cafile)
        os.environ.setdefault("CURL_CA_BUNDLE", cafile)

        # urllib 등에서 기본 컨텍스트를 쓰는 경우 대비
        try:
            ssl._create_default_https_context = (  # type: ignore[attr-defined]
                lambda: ssl.create_default_context(cafile=cafile)
            )
        except Exception:
            pass

        _ssl_ready = True
        return

    # 3) 파이썬/OpenSSL 기본 verify path가 있으면 그걸라도 사용
    try:
        paths = ssl.get_default_verify_paths()
        if paths.cafile and Path(paths.cafile).exists():
            os.environ.setdefault("SSL_CERT_FILE", paths.cafile)
    except Exception:
        pass

    _ssl_ready = True


def _load_config() -> PopbillConfig:
    link_id = (os.getenv("POPBILL_LINK_ID") or "").strip()
    secret_key = (os.getenv("POPBILL_SECRET_KEY") or "").strip()
    corp_num = (os.getenv("POPBILL_CORP_NUM") or "").strip().replace("-", "")
    user_id = (os.getenv("POPBILL_USER_ID") or "").strip()
    is_test = (os.getenv("POPBILL_IS_TEST") or "").strip() in ("1", "true", "True", "yes", "YES")

    missing = [
        k
        for k, v in {
            "POPBILL_LINK_ID": link_id,
            "POPBILL_SECRET_KEY": secret_key,
            "POPBILL_CORP_NUM": corp_num,
            "POPBILL_USER_ID": user_id,
        }.items()
        if not v
    ]

    if missing:
        raise PopbillConfigError(f"Popbill 설정 누락: {', '.join(missing)}")

    return PopbillConfig(
        link_id=link_id,
        secret_key=secret_key,
        corp_num=corp_num,
        user_id=user_id,
        is_test=is_test,
    )


def get_service():
    global _service, _config

    if EasyFinBankService is None:
        raise PopbillConfigError("popbill 패키지가 설치되지 않았습니다. (pip install popbill)")

    if _service is not None:
        return _service

    # ✅ SSL 루트 인증서 번들 보강
    _ensure_https_ca_bundle()

    cfg = _load_config()
    svc = EasyFinBankService(cfg.link_id, cfg.secret_key)

    svc.IsTest = cfg.is_test
    svc.IPRestrictOnOff = True
    svc.UseStaticIP = False
    svc.UseLocalTimeYN = True

    _service = svc
    _config = cfg
    return _service


def get_config() -> PopbillConfig:
    global _config
    if _config is None:
        _config = _load_config()
    return _config


def _wrap_popbill_exc(e: Exception) -> PopbillApiError:
    code = getattr(e, "code", None)
    msg = getattr(e, "message", str(e))
    s = str(msg)

    # ✅ 사용자에게 “뭘 해야 하는지” 바로 보이게
    if "CERTIFICATE_VERIFY_FAILED" in s or "certificate verify failed" in s.lower():
        return PopbillApiError(
            "SSL 인증서 검증 실패(로컬 루트 인증서/CA 번들 문제). "
            "가상환경에서 `pip install -U certifi` 실행 후 재시작해보세요. "
            "그래도 안 되면 회사/학교 네트워크의 HTTPS 프록시(SSL 가로채기) 가능성이 있어 "
            "해당 루트 인증서를 시스템 신뢰 목록에 추가해야 합니다.",
            code=code,
        )

    return PopbillApiError(str(msg), code=code)


def get_bank_account_mgt_url() -> str:
    svc = get_service()
    cfg = get_config()
    try:
        return svc.getBankAccountMgtURL(cfg.corp_num, cfg.user_id)
    except Exception as e:
        raise _wrap_popbill_exc(e)


def list_bank_accounts():
    svc = get_service()
    cfg = get_config()
    try:
        return svc.listBankAccount(cfg.corp_num, cfg.user_id)
    except Exception as e:
        raise _wrap_popbill_exc(e)


def request_job(bank_code: str, account_number: str, sdate: str, edate: str) -> str:
    svc = get_service()
    cfg = get_config()
    try:
        return svc.requestJob(cfg.corp_num, bank_code, account_number, sdate, edate, cfg.user_id)
    except Exception as e:
        raise _wrap_popbill_exc(e)


def get_job_state(job_id: str):
    svc = get_service()
    cfg = get_config()
    try:
        return svc.getJobState(cfg.corp_num, job_id, cfg.user_id)
    except Exception as e:
        raise _wrap_popbill_exc(e)


def search(
    job_id: str,
    trade_types=None,
    search_string: str = "",
    page: int = 1,
    per_page: int = 1000,
    order: str = "D",
):
    svc = get_service()
    cfg = get_config()
    trade_types = trade_types or []
    try:
        return svc.search(cfg.corp_num, job_id, trade_types, search_string, page, per_page, order, cfg.user_id)
    except Exception as e:
        raise _wrap_popbill_exc(e)
