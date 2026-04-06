"""Notion 데이터 조회 모듈."""

import json
import os
from datetime import date, timedelta

from notion_client import Client


def fetch_notion_history(client: Client, issue_db_id: str) -> list[dict]:
    """최근 2주 이슈 히스토리 조회."""
    two_weeks_ago = (date.today() - timedelta(days=14)).isoformat()

    history = []
    has_more = True
    start_cursor = None

    while has_more:
        query_args = {
            "database_id": issue_db_id,
            "filter": {
                "property": "최종 확인일",
                "date": {"on_or_after": two_weeks_ago},
            },
        }
        if start_cursor:
            query_args["start_cursor"] = start_cursor
        results = client.databases.query(**query_args)
        has_more = results.get("has_more", False)
        start_cursor = results.get("next_cursor")

        for page in results.get("results", []):
            props = page["properties"]
            history.append({
                "hash": _get_rich_text(props.get("이슈 지문 hash", {})),
                "type": _get_select(props.get("이슈 유형", {})),
                "url": _get_url(props.get("URL", {})),
                "status": _get_select(props.get("상태", {})),
                "severity": _get_select(props.get("심각도", {})),
                "description": _get_rich_text(props.get("이슈 설명", {})),
                "first_found": _get_date(props.get("최초 발견일", {})),
                "last_checked": _get_date(props.get("최종 확인일", {})),
                "page_id": page["id"],
            })
    return history


def fetch_notion_watch_items(client: Client, watch_db_id: str) -> list[dict]:
    """관찰 중 항목 조회."""
    items = []
    has_more = True
    start_cursor = None

    while has_more:
        query_args = {
            "database_id": watch_db_id,
            "filter": {
                "property": "상태",
                "select": {"equals": "관찰 중"},
            },
        }
        if start_cursor:
            query_args["start_cursor"] = start_cursor
        results = client.databases.query(**query_args)
        has_more = results.get("has_more", False)
        start_cursor = results.get("next_cursor")

        for page in results.get("results", []):
            props = page["properties"]
            items.append({
                "type": _get_select(props.get("이슈 유형", {})),
                "description": _get_rich_text(props.get("배경 설명", {})),
                "registered_date": _get_date(props.get("등록일", {})),
            })
    return items


def fetch_last_report(client: Client, report_db_id: str) -> dict | None:
    """직전 주간 리포트 조회 (전주 대비용)."""
    results = client.databases.query(
        database_id=report_db_id,
        sorts=[{"property": "리포트 날짜", "direction": "descending"}],
        page_size=1,
    )

    pages = results.get("results", [])
    if not pages:
        return None

    props = pages[0]["properties"]
    return {
        "lcp_good_pct": _get_number(props.get("LCP Good %", {})),
        "inp_good_pct": _get_number(props.get("INP Good %", {})),
        "cls_good_pct": _get_number(props.get("CLS Good %", {})),
        "indexed_count": _get_number(props.get("색인 수", {})),
        "index_error_count": _get_number(props.get("색인 오류 수", {})),
        "avg_response_time": _get_number(props.get("평균 응답시간", {})),
    }


# --- Notion property helpers ---

def _get_rich_text(prop: dict) -> str:
    texts = prop.get("rich_text", [])
    return texts[0]["plain_text"] if texts else ""


def _get_select(prop: dict) -> str:
    select = prop.get("select")
    return select["name"] if select else ""


def _get_url(prop: dict) -> str:
    return prop.get("url") or ""


def _get_date(prop: dict) -> str:
    d = prop.get("date")
    return d["start"] if d else ""


def _get_number(prop: dict) -> float | None:
    return prop.get("number")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    client = Client(auth=os.environ["NOTION_TOKEN"])
    history = fetch_notion_history(client, os.environ["NOTION_ISSUE_DB_ID"])
    watch = fetch_notion_watch_items(client, os.environ["NOTION_WATCH_DB_ID"])
    prev = fetch_last_report(client, os.environ["NOTION_REPORT_DB_ID"])
    print("History:", json.dumps(history, indent=2, ensure_ascii=False))
    print("Watch:", json.dumps(watch, indent=2, ensure_ascii=False))
    print("Prev report:", json.dumps(prev, indent=2, ensure_ascii=False))
