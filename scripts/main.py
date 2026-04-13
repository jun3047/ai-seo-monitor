"""SEO 주간 모니터링 파이프라인 오케스트레이터."""

import json
import os
import subprocess
import sys
import time
from datetime import date, datetime


def parse_siteone_json(filepath: str) -> dict:
    """SiteOne 크롤링 JSON 결과 파싱.

    SiteOne JSON 구조:
    - results[]: URL별 기본 정보 (url, status, size, elapsedTime)
    - tables.best-practices[]: SEO 분석 집계 (analysisName별 ok/warning/critical 카운트)
    - tables.accessibility[]: 접근성 분석 집계
    - tables.404[]: 404 에러 URL 목록 (url, sourceUqId, statusCode)
    - tables.content-types[]: 콘텐츠 유형별 HTTP 상태 집계
    - summary.items[]: 전체 요약 (aplCode, status, text)
    - qualityScores: 카테고리별 점수 (seo, security, accessibility, etc.)
    """
    with open(filepath) as f:
        raw = json.load(f)

    result = {
        "total_urls": 0,
        "error_urls": [],
        "redirect_issues": [],
        "slow_pages": [],
        "seo_issues": [],
        "accessibility_issues": [],
        "best_practice_issues": [],
        "not_found_urls": [],
        "summary_issues": [],
        "quality_scores": {},
    }

    # --- 1. results[]: URL별 기본 정보 ---
    results = raw.get("results", [])
    if isinstance(results, list):
        result["total_urls"] = len(results)

        for entry in results:
            url = entry.get("url", "")
            status = entry.get("status", "200")
            elapsed = entry.get("elapsedTime", 0)

            # status가 문자열일 수 있음
            status_int = int(status) if str(status).isdigit() else 200

            # 4xx/5xx 에러
            if 400 <= status_int < 600:
                result["error_urls"].append({"url": url, "status": status_int})

            # 3xx 리다이렉트
            if 300 <= status_int < 400:
                result["redirect_issues"].append({"url": url, "status": status_int})

            # 느린 페이지 (1초 초과)
            if isinstance(elapsed, (int, float)) and elapsed > 1.0:
                result["slow_pages"].append({
                    "url": url,
                    "response_time_sec": round(elapsed, 3),
                })

    # 느린 페이지 TOP 5
    result["slow_pages"] = sorted(
        result["slow_pages"],
        key=lambda x: x["response_time_sec"],
        reverse=True,
    )[:5]

    # --- 2. tables ---
    tables = raw.get("tables", {})

    # tables.best-practices: SEO/구조 이슈 집계
    for row in tables.get("best-practices", []):
        name = row.get("analysisName", "")
        warning = int(row.get("warning", 0))
        critical = int(row.get("critical", 0))
        if warning > 0 or critical > 0:
            result["best_practice_issues"].append({
                "name": name,
                "warning": warning,
                "critical": critical,
                "ok": int(row.get("ok", 0)),
            })

    # tables.accessibility: 접근성 이슈 집계
    for row in tables.get("accessibility", []):
        name = row.get("analysisName", "")
        warning = int(row.get("warning", 0))
        critical = int(row.get("critical", 0))
        if warning > 0 or critical > 0:
            result["accessibility_issues"].append({
                "name": name,
                "warning": warning,
                "critical": critical,
                "ok": int(row.get("ok", 0)),
            })

    # tables.404: 깨진 링크
    for row in tables.get("404", []):
        result["not_found_urls"].append({
            "url": row.get("url", ""),
            "source": row.get("sourceUqId", ""),
            "status": row.get("statusCode", "404"),
        })

    # --- 3. summary.items[]: 전체 요약 ---
    summary = raw.get("summary", {})
    for item in summary.get("items", []):
        status = item.get("status", "")
        if status in ("CRITICAL", "WARNING"):
            result["summary_issues"].append({
                "code": item.get("aplCode", ""),
                "status": status,
                "text": item.get("text", ""),
            })

    # --- 4. qualityScores ---
    quality = raw.get("qualityScores", {})
    if quality:
        result["quality_scores"] = {
            "overall": quality.get("overall", None),
        }
        for cat in quality.get("categories", []):
            code = cat.get("code", "")
            if code:
                result["quality_scores"][code] = {
                    "score": cat.get("score"),
                    "label": cat.get("label", ""),
                }

    return result


def _step_timer(step_name: str, debug_log: dict):
    """스텝 실행시간 측정 컨텍스트 매니저."""
    class Timer:
        def __enter__(self):
            self.start = time.time()
            debug_log["steps"][step_name] = {"status": "running"}
            return self
        def __exit__(self, *args):
            elapsed = round(time.time() - self.start, 2)
            debug_log["steps"][step_name]["elapsed_sec"] = elapsed
            if debug_log["steps"][step_name].get("status") == "running":
                debug_log["steps"][step_name]["status"] = "ok"
            print(f"[{step_name}] {elapsed}s")
    return Timer()


def _mark_step_error(debug_log: dict, step_name: str, error: str):
    """스텝 에러 기록."""
    if step_name in debug_log["steps"]:
        debug_log["steps"][step_name]["status"] = "error"
        debug_log["steps"][step_name]["error"] = error
    else:
        debug_log["steps"][step_name] = {"status": "error", "error": error}


def _save_debug_log(debug_log: dict):
    """디버그 로그를 tmp/debug_log.json에 저장."""
    debug_log["finished_at"] = datetime.now().isoformat()
    debug_log["total_elapsed_sec"] = round(time.time() - debug_log["_start"], 2)
    del debug_log["_start"]

    os.makedirs("./tmp", exist_ok=True)
    with open("./tmp/debug_log.json", "w") as f:
        json.dump(debug_log, f, indent=2, ensure_ascii=False)
    print(f"\n[Debug] Log saved to ./tmp/debug_log.json")


def main():
    today = date.today().isoformat()
    errors = []
    site_url = os.environ.get("SITE_URL", "")
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    debug_log = {
        "started_at": datetime.now().isoformat(),
        "_start": time.time(),
        "site_url": site_url,
        "dry_run": dry_run,
        "steps": {},
        "claude_token_usage": None,
        "issue_counts": {},
        "errors": [],
    }

    # Step 1: SiteOne JSON 파싱
    siteone_data = {}
    siteone_path = "./tmp/siteone-report.json"
    with _step_timer("step1_siteone_parse", debug_log):
        try:
            siteone_data = parse_siteone_json(siteone_path)
            total_urls = siteone_data.get("total_urls", 0)
            bp_issues = len(siteone_data.get("best_practice_issues", []))
            a11y_issues = len(siteone_data.get("accessibility_issues", []))
            summary_issues = len(siteone_data.get("summary_issues", []))
            debug_log["steps"]["step1_siteone_parse"]["total_urls"] = total_urls
            debug_log["steps"]["step1_siteone_parse"]["bp_issues"] = bp_issues
            debug_log["steps"]["step1_siteone_parse"]["a11y_issues"] = a11y_issues
            print(f"  SiteOne parsed: {total_urls} URLs, {bp_issues} best-practice issues, {a11y_issues} accessibility issues, {summary_issues} summary warnings")
        except FileNotFoundError:
            errors.append(f"SiteOne output not found: {siteone_path}")
            _mark_step_error(debug_log, "step1_siteone_parse", errors[-1])
            print(f"  WARNING: {errors[-1]}")
        except Exception as e:
            errors.append(f"SiteOne parsing failed: {e}")
            _mark_step_error(debug_log, "step1_siteone_parse", errors[-1])
            print(f"  ERROR: {errors[-1]}")

    # Step 2: GSC + CrUX 데이터 수집
    gsc_data = {"submitted_count": 0, "pages_with_impressions": 0, "index_errors": [], "cwv": {}, "search_performance": {"current": {}, "previous": {}}, "top_queries": [], "top_pages": []}
    with _step_timer("step2_gsc_crux", debug_log):
        try:
            from fetch_gsc import fetch_gsc_data
            gsc_data = fetch_gsc_data(site_url)
            perf = gsc_data.get("search_performance", {}).get("current", {})
            print(f"  GSC data: submitted={gsc_data.get('submitted_count', 0)}, pages_with_impressions={gsc_data.get('pages_with_impressions', 0)}, errors={len(gsc_data.get('index_errors', []))}")
            print(f"  Search: clicks={perf.get('clicks', 0)}, impressions={perf.get('impressions', 0)}, ctr={perf.get('ctr', 0)}%, position={perf.get('position', 0)}")
        except Exception as e:
            errors.append(f"GSC fetch failed: {e}")
            _mark_step_error(debug_log, "step2_gsc_crux", errors[-1])
            print(f"  ERROR: {errors[-1]}")

    # Step 3-4: Notion 데이터 조회
    history = []
    watch_items = []
    prev_metrics = None
    with _step_timer("step3_4_notion_fetch", debug_log):
        try:
            from fetch_notion import fetch_notion_history, fetch_notion_watch_items, fetch_last_report
            from notion_client import Client as NotionClient
            notion = NotionClient(auth=os.environ["NOTION_TOKEN"])
            history = fetch_notion_history(notion, os.environ["NOTION_ISSUE_DB_ID"])
            watch_items = fetch_notion_watch_items(notion, os.environ["NOTION_WATCH_DB_ID"])
            prev_metrics = fetch_last_report(notion, os.environ["NOTION_REPORT_DB_ID"])
            debug_log["steps"]["step3_4_notion_fetch"]["history_count"] = len(history)
            debug_log["steps"]["step3_4_notion_fetch"]["watch_count"] = len(watch_items)
            debug_log["steps"]["step3_4_notion_fetch"]["has_prev_report"] = prev_metrics is not None
            print(f"  Notion: {len(history)} history, {len(watch_items)} watch items, prev_report={'있음' if prev_metrics else '없음'}")
        except Exception as e:
            errors.append(f"Notion fetch failed: {e}")
            _mark_step_error(debug_log, "step3_4_notion_fetch", errors[-1])
            print(f"  ERROR: {errors[-1]}")

    # Step 5: Claude AI 진단
    analysis = None
    with _step_timer("step5_claude_analysis", debug_log):
        try:
            from analyze_claude import analyze_seo
            analysis = analyze_seo(siteone_data, gsc_data, history, watch_items, prev_metrics)

            # 토큰 사용량 추출
            token_usage = analysis.pop("_token_usage", None)
            if token_usage:
                debug_log["claude_token_usage"] = token_usage
                debug_log["steps"]["step5_claude_analysis"]["input_tokens"] = token_usage["input_tokens"]
                debug_log["steps"]["step5_claude_analysis"]["output_tokens"] = token_usage["output_tokens"]
                print(f"  Tokens: {token_usage['input_tokens']} in / {token_usage['output_tokens']} out")

            debug_log["issue_counts"] = {
                "new": len(analysis.get("new_issues", [])),
                "recurred": len(analysis.get("recurred_issues", [])),
                "resolved": len(analysis.get("resolved_issues", [])),
                "skipped": analysis.get("skipped_count", 0),
            }
            print(f"  Analysis: {debug_log['issue_counts']}")
        except Exception as e:
            errors.append(f"Claude analysis failed: {e}")
            _mark_step_error(debug_log, "step5_claude_analysis", errors[-1])
            print(f"  FATAL: {errors[-1]}")

    if analysis is None:
        print("FATAL: Claude analysis failed, sending error notification")
        debug_log["errors"] = errors
        _save_debug_log(debug_log)
        try:
            from send_slack import send_error_notification
            send_error_notification(errors)
        except Exception as e:
            print(f"Failed to send error notification: {e}")
        sys.exit(1)

    if dry_run:
        print("[DRY RUN] Skipping Notion sync and Slack notification")
        print(json.dumps(analysis, indent=2, ensure_ascii=False))
        debug_log["errors"] = errors
        _save_debug_log(debug_log)
        return

    # Step 6: Notion 업데이트
    report_url = ""
    with _step_timer("step6_notion_sync", debug_log):
        try:
            from sync_notion import sync_issues, create_weekly_report
            from notion_client import Client as NotionClient
            notion = NotionClient(auth=os.environ["NOTION_TOKEN"])
            sync_issues(notion, os.environ["NOTION_ISSUE_DB_ID"], analysis, today)
            report_url = create_weekly_report(
                notion, os.environ["NOTION_REPORT_DB_ID"],
                analysis, gsc_data, siteone_data, today,
            )
            print(f"  Notion synced, report: {report_url}")
        except Exception as e:
            errors.append(f"Notion sync failed: {e}")
            _mark_step_error(debug_log, "step6_notion_sync", errors[-1])
            print(f"  ERROR: {errors[-1]}")

    # Step 7: Slack 발송
    with _step_timer("step7_slack_send", debug_log):
        try:
            from send_slack import send_slack_report
            send_slack_report(analysis, gsc_data, prev_metrics, report_url)
            print("  Slack report sent")
        except Exception as e:
            errors.append(f"Slack send failed: {e}")
            _mark_step_error(debug_log, "step7_slack_send", errors[-1])
            print(f"  ERROR: {errors[-1]}")

    # 디버그 로그 저장
    debug_log["errors"] = errors
    _save_debug_log(debug_log)

    if errors:
        print(f"\nCompleted with {len(errors)} error(s):")
        for err in errors:
            print(f"  - {err}")
    else:
        print("\nAll steps completed successfully")


if __name__ == "__main__":
    # scripts/ 디렉토리를 import path에 추가
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dotenv import load_dotenv
    load_dotenv()
    main()
