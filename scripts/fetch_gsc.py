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


def _date_days_ago(days: int) -> str:
    from datetime import date, timedelta
    return (date.today() - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Search Analytics
# ---------------------------------------------------------------------------

def _query_search_analytics(service, site_url: str, start_date: str, end_date: str, dimensions: list[str], row_limit: int = 25000) -> list[dict]:
    """Search Analytics 쿼리 실행 헬퍼."""
    try:
        result = service.searchanalytics().query(
            siteUrl=site_url,
            body={
                "startDate": start_date,
                "endDate": end_date,
                "dimensions": dimensions,
                "rowLimit": row_limit,
            },
        ).execute()
        return result.get("rows", [])
    except Exception as e:
        print(f"Warning: Search Analytics query failed (dims={dimensions}): {e}")
        return []


def _get_search_performance(service, site_url: str) -> dict:
    """사이트 전체 검색 성과: 최근 7일 vs 이전 7일."""
    def _aggregate(rows):
        total_clicks = sum(r.get("clicks", 0) for r in rows)
        total_impressions = sum(r.get("impressions", 0) for r in rows)
        avg_ctr = round(total_clicks / total_impressions * 100, 2) if total_impressions > 0 else 0
        # position은 노출 가중 평균
        weighted_pos = sum(r.get("position", 0) * r.get("impressions", 0) for r in rows)
        avg_position = round(weighted_pos / total_impressions, 1) if total_impressions > 0 else 0
        return {
            "clicks": total_clicks,
            "impressions": total_impressions,
            "ctr": avg_ctr,
            "position": avg_position,
        }

    # GSC 데이터는 2~3일 지연 → 3일 전부터 계산
    current_rows = _query_search_analytics(
        service, site_url,
        _date_days_ago(10), _date_days_ago(3),
        dimensions=["date"],
    )
    previous_rows = _query_search_analytics(
        service, site_url,
        _date_days_ago(17), _date_days_ago(10),
        dimensions=["date"],
    )

    return {
        "current": _aggregate(current_rows),
        "previous": _aggregate(previous_rows),
    }


def _get_top_queries(service, site_url: str, limit: int = 20) -> list[dict]:
    """검색어별 성과 TOP N (최근 7일)."""
    rows = _query_search_analytics(
        service, site_url,
        _date_days_ago(10), _date_days_ago(3),
        dimensions=["query"],
        row_limit=limit,
    )
    return [
        {
            "query": r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        }
        for r in rows
    ]


def _get_top_pages(service, site_url: str, limit: int = 20) -> list[dict]:
    """페이지별 성과 TOP N (최근 7일)."""
    rows = _query_search_analytics(
        service, site_url,
        _date_days_ago(10), _date_days_ago(3),
        dimensions=["page"],
        row_limit=limit,
    )
    return [
        {
            "page": r["keys"][0],
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": round(r.get("ctr", 0) * 100, 2),
            "position": round(r.get("position", 0), 1),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Sitemap / Index
# ---------------------------------------------------------------------------

def _get_submitted_count(service, site_url: str) -> int:
    """Sitemap API로 제출된 URL 수 합산."""
    try:
        result = service.sitemaps().list(siteUrl=site_url).execute()
        total = 0
        for sitemap in result.get("sitemap", []):
            for content in sitemap.get("contents", []):
                val = content.get("submitted", 0)
                total += int(val) if val else 0
        return total
    except Exception as e:
        print(f"Warning: Failed to get submitted count: {e}")
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


# ---------------------------------------------------------------------------
# CrUX (Core Web Vitals)
# ---------------------------------------------------------------------------

def _get_cwv_data(site_url: str) -> dict:
    """CrUX API로 Core Web Vitals 데이터 수집 (API 키 방식)."""
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


# ---------------------------------------------------------------------------
# 통합 수집
# ---------------------------------------------------------------------------

def fetch_gsc_data(site_url: str) -> dict:
    """GSC + CrUX 데이터 통합 수집."""
    service = _get_gsc_service()

    # GSC에 등록된 실제 속성 URL 확인
    gsc_site_url = _resolve_site_url(service, site_url)
    print(f"  GSC site URL resolved: {gsc_site_url}")

    # Search Analytics
    print("  Fetching search performance...")
    search_performance = _get_search_performance(service, gsc_site_url)
    print(f"  Search performance: {search_performance['current']['clicks']} clicks, {search_performance['current']['impressions']} impressions")

    print("  Fetching top queries...")
    top_queries = _get_top_queries(service, gsc_site_url)
    print(f"  Top queries: {len(top_queries)} queries")

    print("  Fetching top pages...")
    top_pages = _get_top_pages(service, gsc_site_url)
    print(f"  Top pages: {len(top_pages)} pages")

    pages_with_impressions = len(top_pages)

    # Sitemap
    submitted_count = _get_submitted_count(service, gsc_site_url)
    print(f"  Sitemap submitted: {submitted_count} URLs")

    # URL Inspection (검색 노출 URL 대상)
    search_urls = [p["page"] for p in top_pages]
    index_errors = _get_index_errors(service, gsc_site_url, search_urls)

    # CrUX
    cwv = _get_cwv_data(site_url)

    return {
        "submitted_count": submitted_count,
        "pages_with_impressions": pages_with_impressions,
        "index_errors": index_errors,
        "cwv": cwv,
        "search_performance": search_performance,
        "top_queries": top_queries,
        "top_pages": top_pages,
    }


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    site_url = os.environ.get("SITE_URL", "")
    result = fetch_gsc_data(site_url)
    print(json.dumps(result, indent=2, ensure_ascii=False))
