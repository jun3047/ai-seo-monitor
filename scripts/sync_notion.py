"""Notion 이슈/리포트 업데이트 모듈."""

import hashlib
import json
import os
import time
from datetime import date

from notion_client import Client


def _make_fingerprint(issue_type: str, url: str, description: str) -> str:
    """이슈 지문 hash 생성."""
    raw = f"{issue_type}:{url}:{description}"
    return hashlib.md5(raw.encode()).hexdigest()


def _find_page_by_hash(client: Client, db_id: str, fingerprint: str) -> str | None:
    """지문 hash로 기존 페이지 ID 조회."""
    results = client.databases.query(
        database_id=db_id,
        filter={
            "property": "이슈 지문 hash",
            "rich_text": {"equals": fingerprint},
        },
        page_size=1,
    )
    pages = results.get("results", [])
    return pages[0]["id"] if pages else None


def _rate_limit_sleep():
    """Notion API rate limit 대응 (3 req/sec)."""
    time.sleep(0.35)


def sync_issues(client: Client, issue_db_id: str, analysis: dict, today: str) -> None:
    """이슈 히스토리 DB 업데이트."""
    # 신규 이슈 → 새 row 생성
    for issue in analysis.get("new_issues", []):
        fingerprint = _make_fingerprint(
            issue.get("type", ""),
            issue.get("url") or "",
            issue.get("description", ""),
        )
        issue_title = f"[{issue.get('severity', 'Info')}] {issue.get('type', '기타')}"
        client.pages.create(
            parent={"database_id": issue_db_id},
            properties={
                "이슈": {"title": [{"text": {"content": issue_title}}]},
                "이슈 유형": {"select": {"name": issue.get("type", "기타")}},
                "URL": {"url": issue.get("url") or None},
                "상태": {"select": {"name": "신규"}},
                "심각도": {"select": {"name": issue.get("severity", "Info")}},
                "이슈 설명": {"rich_text": [{"text": {"content": issue.get("description", "")[:2000]}}]},
                "조치 방향": {"rich_text": [{"text": {"content": issue.get("action", "")[:2000]}}]},
                "이슈 지문 hash": {"rich_text": [{"text": {"content": fingerprint}}]},
                "최초 발견일": {"date": {"start": today}},
                "최종 확인일": {"date": {"start": today}},
            },
        )
        _rate_limit_sleep()

    # 재발 이슈 → 상태 업데이트
    for issue in analysis.get("recurred_issues", []):
        fingerprint = _make_fingerprint(
            issue.get("type", ""),
            issue.get("url") or "",
            issue.get("description", ""),
        )
        page_id = _find_page_by_hash(client, issue_db_id, fingerprint)
        _rate_limit_sleep()

        if page_id:
            client.pages.update(
                page_id=page_id,
                properties={
                    "상태": {"select": {"name": "재발"}},
                    "심각도": {"select": {"name": issue.get("severity", "Info")}},
                    "최종 확인일": {"date": {"start": today}},
                },
            )
            _rate_limit_sleep()
        else:
            # 기존 페이지를 못 찾으면 신규로 생성
            issue_title = f"[{issue.get('severity', 'Info')}] {issue.get('type', '기타')}"
            client.pages.create(
                parent={"database_id": issue_db_id},
                properties={
                    "이슈": {"title": [{"text": {"content": issue_title}}]},
                    "이슈 유형": {"select": {"name": issue.get("type", "기타")}},
                    "URL": {"url": issue.get("url") or None},
                    "상태": {"select": {"name": "재발"}},
                    "심각도": {"select": {"name": issue.get("severity", "Info")}},
                    "이슈 설명": {"rich_text": [{"text": {"content": issue.get("description", "")[:2000]}}]},
                    "조치 방향": {"rich_text": [{"text": {"content": issue.get("action", "")[:2000]}}]},
                    "이슈 지문 hash": {"rich_text": [{"text": {"content": fingerprint}}]},
                    "최초 발견일": {"date": {"start": today}},
                    "최종 확인일": {"date": {"start": today}},
                },
            )
            _rate_limit_sleep()

    # 해소 이슈 → 상태 업데이트
    for issue in analysis.get("resolved_issues", []):
        fingerprint = _make_fingerprint(
            issue.get("type", ""),
            issue.get("url") or "",
            issue.get("description", ""),
        )
        page_id = _find_page_by_hash(client, issue_db_id, fingerprint)
        _rate_limit_sleep()

        if page_id:
            client.pages.update(
                page_id=page_id,
                properties={
                    "상태": {"select": {"name": "해소"}},
                    "최종 확인일": {"date": {"start": today}},
                    "해소일": {"date": {"start": today}},
                },
            )
            _rate_limit_sleep()


def create_weekly_report(
    client: Client,
    report_db_id: str,
    analysis: dict,
    gsc_data: dict,
    siteone_data: dict,
    today: str,
) -> str:
    """주간 리포트 DB에 기록하고 페이지 URL 반환."""
    cwv = gsc_data.get("cwv", {})

    # 평균 응답시간 계산 (SiteOne 데이터)
    slow_pages = siteone_data.get("slow_pages", [])
    all_response_times = [p.get("response_time", 0) for p in slow_pages if p.get("response_time")]
    avg_response = round(sum(all_response_times) / len(all_response_times), 2) if all_response_times else 0

    page = client.pages.create(
        parent={"database_id": report_db_id},
        properties={
            "리포트": {"title": [{"text": {"content": f"주간 SEO 리포트 | {today}"}}]},
            "리포트 날짜": {"date": {"start": today}},
            "Claude 요약": {"rich_text": [{"text": {"content": analysis.get("summary", "")[:2000]}}]},
            "신규 이슈 수": {"number": len(analysis.get("new_issues", []))},
            "재발 이슈 수": {"number": len(analysis.get("recurred_issues", []))},
            "해소 이슈 수": {"number": len(analysis.get("resolved_issues", []))},
            "생략 이슈 수": {"number": analysis.get("skipped_count", 0)},
            "LCP Good %": {"number": cwv.get("lcp", {}).get("good_pct")},
            "INP Good %": {"number": cwv.get("inp", {}).get("good_pct")},
            "CLS Good %": {"number": cwv.get("cls", {}).get("good_pct")},
            "색인 수": {"number": gsc_data.get("indexed_count", 0)},
            "색인 오류 수": {"number": len(gsc_data.get("index_errors", []))},
            "평균 응답시간": {"number": avg_response},
            "Notion 이슈 링크": {"url": f"https://www.notion.so/{report_db_id.replace('-', '')}"},
        },
    )

    page_url = page.get("url", "")
    return page_url


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    print("sync_notion module loaded successfully")
