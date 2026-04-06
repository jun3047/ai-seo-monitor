"""GSC + CrUX 데이터 수집 모듈."""

import json
import os

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build


GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
CRUX_API_URL = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"


def _parse_service_account_json() -> dict:
    """환경변수에서 서비스 계정 JSON 파싱."""
    raw = os.environ["GSC_SERVICE_ACCOUNT_JSON"]
    # dotenv 이스케이프 처리: \" → "
    if raw.startswith('{\\'):
        raw = raw.replace('\\"', '"')
    creds_json = json.loads(raw)
    # private_key의 리터럴 \\n을 실제 줄바꿈으로 변환
    if "private_key" in creds_json:
        creds_json["private_key"] = creds_json["private_key"].replace("\\n", "\n")
    return creds_json


def _get_credentials():
    """GSC용 서비스 계정 credentials 생성."""
    return service_account.Credentials.from_service_account_info(
        _parse_service_account_json(), scopes=GSC_SCOPES
    )


def _get_gsc_service():
    """GSC API 서비스 클라이언트 생성."""
    return build("searchconsole", "v1", credentials=_get_credentials())


def _resolve_site_url(service, site_url: str) -> str:
    """SITE_URL에서 GSC에 등록된 실제 속성 URL로 변환."""
    try:
        sites = service.sites().list().execute()
        entries = sites.get("siteEntry", [])
        # 정확히 매칭
        for entry in entries:
            if entry["siteUrl"] == site_url:
                return site_url
        # 도메인 속성 매칭 (sc-domain:)
        from urllib.parse import urlparse
        domain = urlparse(site_url).netloc.replace("www.", "")
        for entry in entries:
            if entry["siteUrl"] == f"sc-domain:{domain}":
                return entry["siteUrl"]
        # 첫 번째 사이트라도 반환
        if entries:
            return entries[0]["siteUrl"]
    except Exception as e:
        print(f"Warning: Failed to resolve site URL: {e}")
    return site_url


def _get_indexed_count(service, site_url: str) -> int:
    """Sitemap API로 색인 페이지 수 합산."""
    try:
        result = service.sitemaps().list(siteUrl=site_url).execute()
        total = 0
        for sitemap in result.get("sitemap", []):
            for content in sitemap.get("contents", []):
                val = content.get("indexed", 0)
                total += int(val) if val else 0
        return total
    except Exception as e:
        print(f"Warning: Failed to get indexed count: {e}")
        return 0


def _get_index_errors(service, site_url: str, sample_urls: list[str]) -> list[dict]:
    """URL Inspection API로 색인 오류 확인 (샘플링)."""
    errors = []
    for url in sample_urls[:50]:
        try:
            result = service.urlInspection().index().inspect(
                body={"inspectionUrl": url, "siteUrl": site_url}
            ).execute()
            inspection = result.get("inspectionResult", {})
            index_status = inspection.get("indexStatusResult", {})
            verdict = index_status.get("verdict", "")
            if verdict != "PASS":
                errors.append({
                    "url": url,
                    "type": index_status.get("coverageState", "UNKNOWN"),
                    "detail": index_status.get("robotsTxtState", ""),
                })
        except Exception as e:
            print(f"Warning: URL inspection failed for {url}: {e}")
    return errors


def _get_search_urls(service, site_url: str) -> list[str]:
    """Search Analytics로 검색 노출 URL 목록 수집."""
    try:
        result = service.searchanalytics().query(
            siteUrl=site_url,
            body={
                "startDate": _date_days_ago(7),
                "endDate": _date_days_ago(1),
                "dimensions": ["page"],
                "rowLimit": 100,
            },
        ).execute()
        return [row["keys"][0] for row in result.get("rows", [])]
    except Exception as e:
        print(f"Warning: Failed to get search URLs: {e}")
        return []


def _get_cwv_data(site_url: str) -> dict:
    """CrUX API로 Core Web Vitals 데이터 수집 (API 키 방식)."""
    # CrUX는 전체 origin URL 필요 (scheme 포함)
    origin = site_url.rstrip("/")
    if not origin.startswith("http"):
        origin = f"https://{origin}"

    api_key = os.environ.get("CRUX_API_KEY", "")
    if not api_key:
        print("Warning: CRUX_API_KEY not set, skipping CWV data")
        return _empty_cwv()

    try:
        resp = requests.post(
            f"{CRUX_API_URL}?key={api_key}",
            json={"origin": origin, "formFactor": "PHONE"},
            timeout=30,
        )
        if resp.status_code == 404:
            print("Warning: No CrUX data available for this origin")
            return _empty_cwv()
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Warning: CrUX API failed: {e}")
        return _empty_cwv()

    metrics = data.get("record", {}).get("metrics", {})
    return {
        "lcp": _parse_crux_metric(metrics.get("largest_contentful_paint", {})),
        "inp": _parse_crux_metric(metrics.get("interaction_to_next_paint", {})),
        "cls": _parse_crux_metric(metrics.get("cumulative_layout_shift", {})),
    }


def _parse_crux_metric(metric: dict) -> dict:
    """CrUX 메트릭에서 Good/NI/Poor 비율 추출."""
    histogram = metric.get("histogram", [])
    if len(histogram) >= 3:
        good_pct = round((histogram[0].get("density", 0)) * 100, 1)
        ni_pct = round((histogram[1].get("density", 0)) * 100, 1)
        poor_pct = round((histogram[2].get("density", 0)) * 100, 1)
    else:
        good_pct = ni_pct = poor_pct = 0.0

    p75 = metric.get("percentiles", {}).get("p75")
    return {
        "good_pct": good_pct,
        "needs_improvement_pct": ni_pct,
        "poor_pct": poor_pct,
        "p75": p75,
    }


def _empty_cwv() -> dict:
    empty = {"good_pct": None, "needs_improvement_pct": None, "poor_pct": None, "p75": None}
    return {"lcp": dict(empty), "inp": dict(empty), "cls": dict(empty)}


def _date_days_ago(days: int) -> str:
    from datetime import date, timedelta
    return (date.today() - timedelta(days=days)).isoformat()


def fetch_gsc_data(site_url: str) -> dict:
    """GSC + CrUX 데이터 통합 수집."""
    service = _get_gsc_service()

    # GSC에 등록된 실제 속성 URL 확인
    gsc_site_url = _resolve_site_url(service, site_url)
    print(f"  GSC site URL resolved: {gsc_site_url}")

    indexed_count = _get_indexed_count(service, gsc_site_url)
    search_urls = _get_search_urls(service, gsc_site_url)
    index_errors = _get_index_errors(service, gsc_site_url, search_urls)
    cwv = _get_cwv_data(site_url)

    return {
        "indexed_count": indexed_count,
        "index_errors": index_errors,
        "cwv": cwv,
    }


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    site_url = os.environ.get("SITE_URL", "")
    result = fetch_gsc_data(site_url)
    print(json.dumps(result, indent=2, ensure_ascii=False))
