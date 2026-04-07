"""Slack 스레드 발송 모듈."""

import math
import os
from datetime import date

from slack_sdk import WebClient


MAX_MESSAGE_LENGTH = 35000


def _format_diff(current, previous, unit="") -> str:
    """전주 대비 변화량 포매팅."""
    if previous is None or previous == "N/A":
        return ""
    diff = current - previous
    sign = "+" if diff > 0 else ""
    return f" ({sign}{diff}{unit})"


def _build_detail_message(
    analysis: dict,
    gsc_data: dict,
    prev_metrics: dict | None,
    report_url: str,
) -> str:
    """스레드 상세 리포트 메시지 구성."""
    today = date.today()
    site_name = os.environ.get("SITE_NAME", "SEO")
    mention = os.environ.get("SLACK_MENTION", "")
    cwv = gsc_data.get("cwv", {})
    prev = prev_metrics or {}

    indexed = gsc_data.get("indexed_count", 0)
    index_errors = len(gsc_data.get("index_errors", []))
    indexed_prev = prev.get("indexed_count")
    errors_prev = prev.get("index_error_count")

    lcp_good = cwv.get("lcp", {}).get("good_pct")
    inp_good = cwv.get("inp", {}).get("good_pct")
    cls_good = cwv.get("cls", {}).get("good_pct")

    indexed_diff = _format_diff(indexed, indexed_prev)
    errors_diff = _format_diff(index_errors, errors_prev, "건")

    week_of_month = math.ceil(today.day / 7)

    lines = []

    # 태깅 + 인사
    if mention:
        lines.append(f"<@{mention}> {site_name} {today.month}월 {week_of_month}주차 SEO 모니터링 보고드립니다")
    else:
        lines.append(f"{site_name} {today.month}월 {week_of_month}주차 SEO 모니터링 보고드립니다")
    lines.append("")

    # Claude 진단
    lines.append("> *Claude 진단*")
    lines.append(f"- {analysis.get('summary', '요약 없음')}")
    lines.append("")

    # 주요 지표
    lines.append("> *주요 지표*")
    lines.append(f"- 색인: {indexed}개{indexed_diff}")
    lines.append(f"- 색인 오류: {index_errors}건{errors_diff}")
    index_error_list = gsc_data.get("index_errors", [])
    for err in index_error_list:
        if isinstance(err, dict):
            lines.append(f"  - {err.get('url', '')} ({err.get('verdict', '')})")
        else:
            lines.append(f"  - {err}")
    lines.append("")

    # Core Web Vitals
    lines.append("> *Core Web Vitals*")
    if lcp_good is not None:
        lines.append(f"- LCP Good {lcp_good}%")
        lines.append(f"- INP Good {inp_good}%")
        lines.append(f"- CLS Good {cls_good}%")
    else:
        lines.append("- 데이터 부족 (CrUX 데이터 없음)")

    # 지표 코멘트
    metric_comments = analysis.get("metric_comments", [])
    if metric_comments:
        for mc in metric_comments:
            lines.append(f"- {mc.get('metric', '')}: {mc.get('comment', '')}")
    lines.append("")

    # 신규 이슈
    new_issues = analysis.get("new_issues", [])
    lines.append(f"> *신규 이슈 ({len(new_issues)}건)*")
    if new_issues:
        for issue in new_issues:
            severity = issue.get("severity", "Info")
            url = issue.get("url") or "N/A"
            desc = issue.get("description", "")
            action = issue.get("action", "")
            lines.append(f"- :{'red_circle' if severity == 'Critical' else 'large_yellow_circle' if severity == 'Warning' else 'white_circle'}: {url} - {desc}")
            if action:
                lines.append(f"  - {action}")
    else:
        lines.append("- 없음")
    lines.append("")

    # 재발 이슈
    recurred = analysis.get("recurred_issues", [])
    lines.append(f"> *재발 이슈 ({len(recurred)}건)*")
    if recurred:
        for issue in recurred:
            severity = issue.get("severity", "Info")
            url = issue.get("url") or "N/A"
            desc = issue.get("description", "")
            lines.append(f"- :{'red_circle' if severity == 'Critical' else 'large_yellow_circle' if severity == 'Warning' else 'white_circle'}: {url} - {desc}")
    else:
        lines.append("- 없음")
    lines.append("")

    # 해소 이슈
    resolved = analysis.get("resolved_issues", [])
    lines.append(f"> *해소된 이슈 ({len(resolved)}건)*")
    if resolved:
        for issue in resolved:
            url = issue.get("url") or "N/A"
            desc = issue.get("description", "")
            lines.append(f"- {url} - {desc}")
    else:
        lines.append("- 없음")

    # 관찰 중 항목
    watch_updates = analysis.get("watch_updates", [])
    if watch_updates:
        lines.append("")
        lines.append("> *관찰 중 항목*")
        for item in watch_updates:
            t = item.get("type", "")
            change = item.get("change", "")
            comment = item.get("comment", "")
            lines.append(f"- {t}: {change} - {comment}")

    # 하단 링크
    skipped = analysis.get("skipped_count", 0)
    lines.append("")
    if report_url:
        lines.append(f":paperclip: <{report_url}|Notion 상세 보고서>")
    if skipped > 0:
        lines.append(f"(기존 미해소 이슈 {skipped}건 생략)")

    return "\n".join(lines)


def send_slack_report(
    analysis: dict,
    gsc_data: dict,
    prev_metrics: dict | None,
    report_url: str,
) -> None:
    """Slack 메인 메시지 + 스레드 상세 리포트 발송."""
    client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    channel = os.environ["SLACK_CHANNEL_ID"]
    today = date.today()
    site_name = os.environ.get("SITE_NAME", "SEO")

    # 월의 N주차 계산
    week_of_month = math.ceil(today.day / 7)
    title = f"*[{today.year}년 {today.month}월 {week_of_month}주차] {site_name} SEO 모니터링*"

    # Step 1: 메인 메시지
    main_response = client.chat_postMessage(
        channel=channel,
        text=title,
    )
    thread_ts = main_response["ts"]

    # Step 2: 스레드에 상세 리포트
    detail_text = _build_detail_message(analysis, gsc_data, prev_metrics, report_url)

    # 메시지 길이 제한 처리
    if len(detail_text) <= MAX_MESSAGE_LENGTH:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=detail_text,
        )
    else:
        # 긴 메시지 분할 발송
        chunks = _split_message(detail_text, MAX_MESSAGE_LENGTH)
        for chunk in chunks:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=chunk,
            )


def _split_message(text: str, max_len: int) -> list[str]:
    """메시지를 줄 단위로 분할."""
    lines = text.split("\n")
    chunks = []
    current = []
    current_len = 0

    for line in lines:
        if current_len + len(line) + 1 > max_len and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line) + 1

    if current:
        chunks.append("\n".join(current))
    return chunks


def send_error_notification(errors: list[str]) -> None:
    """파이프라인 에러 알림 발송."""
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_ID")
    if not token or not channel:
        return

    client = WebClient(token=token)
    today_str = date.today().isoformat()
    error_text = "\n".join(f"• {e}" for e in errors)

    client.chat_postMessage(
        channel=channel,
        text=f"*[SEO 모니터링 오류] {today_str}*\n\n파이프라인 실행 중 오류가 발생했습니다:\n{error_text}",
    )


if __name__ == "__main__":
    print("send_slack module loaded successfully")
