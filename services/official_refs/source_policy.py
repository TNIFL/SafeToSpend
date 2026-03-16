from __future__ import annotations

from urllib.parse import urlparse

OFFICIAL_ALLOWED_DOMAINS: tuple[str, ...] = (
    "law.go.kr",
    "www.law.go.kr",
    "nhis.or.kr",
    "www.nhis.or.kr",
    "mohw.go.kr",
    "www.mohw.go.kr",
    "nts.go.kr",
    "www.nts.go.kr",
)


def is_official_domain(hostname: str) -> bool:
    host = str(hostname or "").strip().lower()
    if not host:
        return False
    for domain in OFFICIAL_ALLOWED_DOMAINS:
        token = str(domain or "").strip().lower()
        if not token:
            continue
        if host == token or host.endswith(f".{token}"):
            return True
    return False


def is_official_url(url: str) -> bool:
    try:
        host = str(urlparse(str(url or "")).hostname or "").strip().lower()
    except Exception:
        return False
    return is_official_domain(host)


def official_domains_list() -> list[str]:
    return list(OFFICIAL_ALLOWED_DOMAINS)
