"""SEO 주간 모니터링 파이프라인 오케스트레이터."""

import json
import os
import subprocess
import sys
import time
from datetime import date, datetime


def parse_siteone_json(filepath: str) -> dict:
    """SiteOne 크롤링 JSON 결과 파싱."""
    with open(filepath) as f:
        raw = json.load(f)

    result = {
        "error_urls": [],
        "redirect_issues": [],
        "title_issues": [],
        "meta_issues": [],
        "h1_issues": [],
        "canonical_issues": [],
        "missing_alt": [],
        "slow_pages": [],
        "robots_issues": [],
    }

    # SiteOne JSON 구조에 맞게 방어적 파싱
    # 실제 출력 확인 후 필드명 조정 필요
    pages = raw if isinstance(raw, list) else raw.get("urls", raw.get("pages", raw.get("results", [])))
    if not isinstance(pages, list):
        print("Warning: Unexpected SiteOne JSON structure, attempting flat parse")
        pages = []

    seen_titles = {}
    seen_metas = {}
    seen_h1s = {}

    for entry in pages:
        url = entry.get("url", entry.get("finalUrl", ""))
        status = entry.get("statusCode", entry.get("status_code", entry.get("status", 200)))

        # 4xx/5xx 에러
        if isinstance(status, int) and 400 <= status < 600:
            result["error_urls"].append({"url": url, "status": status})

        # 리다이렉트 체인/루프
        redirect_count = entry.get("redirects", entry.get("redirectCount", 0))
        if isinstance(redirect_count, int) and redirect_count > 1:
            result["redirect_issues"].append({
                "url": url,
                "redirect_count": redirect_count,
            })
        if entry.get("isRedirectLoop", False):
            result["redirect_issues"].append({
                "url": url,
                "type": "redirect_loop",
            })

        # title 누락/중복
        title = entry.get("title", entry.get("pageTitle", ""))
        if not title:
            result["title_issues"].append({"url": url, "issue": "missing"})
        else:
            seen_titles.setdefault(title, []).append(url)

        # meta description 누락/중복
        meta = entry.get("description", entry.get("metaDescription", ""))
        if not meta:
            result["meta_issues"].append({"url": url, "issue": "missing"})
        else:
            seen_metas.setdefault(meta, []).append(url)

        # H1 누락/중복
        h1 = entry.get("h1", entry.get("headings", {}).get("h1", ""))
        h1_text = h1 if isinstance(h1, str) else (h1[0] if isinstance(h1, list) and h1 else "")
        if not h1_text:
            result["h1_issues"].append({"url": url, "issue": "missing"})
        else:
            seen_h1s.setdefault(h1_text, []).append(url)

        # canonical 이슈
        canonical = entry.get("canonical", entry.get("canonicalUrl", ""))
        if canonical and canonical != url:
            result["canonical_issues"].append({
                "url": url,
                "canonical": canonical,
            })

        # 이미지 alt 누락
        missing_alt_count = entry.get("imagesWithoutAlt", entry.get("missingAltCount", 0))
        if isinstance(missing_alt_count, int) and missing_alt_count > 0:
            result["missing_alt"].append({
                "url": url,
                "count": missing_alt_count,
            })

        # 응답시간
        response_time = entry.get("responseTime", entry.get("loadTime", entry.get("time", 0)))
        if isinstance(response_time, (int, float)) and response_time > 0:
            result["slow_pages"].append({
                "url": url,
                "response_time": response_time,
            })

        # robots/noindex
        noindex = entry.get("noindex", entry.get("isNoindex", False))
        robots_blocked = entry.get("robotsBlocked", entry.get("isBlockedByRobotsTxt", False))
        if noindex:
            result["robots_issues"].append({"url": url, "type": "noindex"})
        if robots_blocked:
            result["robots_issues"].append({"url": url, "type": "robots_blocked"})

    # 중복 title/meta/H1 추가
    for title, urls in seen_titles.items():
        if len(urls) > 1:
            result["title_issues"].append({"urls": urls, "issue": "duplicate", "value": title})
    for meta, urls in seen_metas.items():
        if len(urls) > 1:
            result["meta_issues"].append({"urls": urls, "issue": "duplicate", "value": meta})
    for h1, urls in seen_h1s.items():
        if len(urls) > 1:
            result["h1_issues"].append({"urls": urls, "issue": "duplicate", "value": h1})

    # 느린 페이지 TOP 5 (1s 초과만, 응답시간 내림차순)
    result["slow_pages"] = sorted(
        [p for p in result["slow_pages"] if p["response_time"] > 1000],
        key=lambda x: x["response_time"],
        reverse=True,
    )[:5]

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
            issue_count = sum(len(v) for v in siteone_data.values() if isinstance(v, list))
            debug_log["steps"]["step1_siteone_parse"]["issues_found"] = issue_count
            print(f"  SiteOne parsed: {issue_count} issues found")
        except FileNotFoundError:
            errors.append(f"SiteOne output not found: {siteone_path}")
            _mark_step_error(debug_log, "step1_siteone_parse", errors[-1])
            print(f"  WARNING: {errors[-1]}")
        except Exception as e:
            errors.append(f"SiteOne parsing failed: {e}")
            _mark_step_error(debug_log, "step1_siteone_parse", errors[-1])
            print(f"  ERROR: {errors[-1]}")

    # Step 2: GSC + CrUX 데이터 수집
    gsc_data = {"indexed_count": 0, "index_errors": [], "cwv": {}}
    with _step_timer("step2_gsc_crux", debug_log):
        try:
            from fetch_gsc import fetch_gsc_data
            gsc_data = fetch_gsc_data(site_url)
            print(f"  GSC data: indexed={gsc_data.get('indexed_count', 0)}, errors={len(gsc_data.get('index_errors', []))}")
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
