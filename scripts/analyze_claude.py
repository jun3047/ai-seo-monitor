"""Claude AI SEO 진단 모듈."""

import json
import os
import re
from datetime import date

import anthropic


SYSTEM_PROMPT = """당신은 프론트엔드 팀의 SEO 모니터링 전문가입니다.
매주 크롤링 데이터와 GSC 데이터를 분석하여 실행 가능한 SEO 이슈 리포트를 작성합니다.

[분류 규칙]
- 신규: 최근 2주 히스토리에 없는 이슈
- 재발: 히스토리에서 '해소' 상태였다가 재등장한 이슈
- 해소: '미해소' 상태였다가 이번 크롤링에서 사라진 이슈
- 관찰 중: 별도 관찰 테이블에 등록된 구조적 현상
  → 동일 이슈가 크롤링에 등장해도 신규/재발로 분류하지 않음
  → 단, 수치가 전주 대비 20% 이상 급변 시 별도 언급
- 생략: 2주 이내 히스토리에 있고 미해소 진행 중인 이슈 → 카운트만 집계

[심각도 기준]
- Critical: 색인 불가, 대량 404, 리다이렉트 루프 등 즉각 대응 필요
- Warning: CWV Poor, meta 누락 등 빠른 대응 권장
- Info: 개선 권장 사항

[진단 기준]
- 심각도: Critical / Warning / Info 3단계
- 각 이슈마다 배경 설명 + 조치 방향 + 우선순위 근거 포함
- 지표는 전주 대비 변화가 임계값 초과 시에만 코멘트
  → 색인 오류 +10% 이상 / CWV Good -5%p 이상 / 응답시간 +20% 이상
  → 클릭 -20% 이상 / 노출 -30% 이상 / 순위 +5 이상(악화)
- 응답은 반드시 JSON으로만 반환"""


def _build_user_prompt(
    siteone_data: dict,
    gsc_data: dict,
    history: list,
    watch_items: list,
    prev_metrics: dict | None,
) -> str:
    today_str = date.today().isoformat()

    cwv = gsc_data.get("cwv", {})
    lcp = cwv.get("lcp", {})
    inp = cwv.get("inp", {})
    cls_ = cwv.get("cls", {})

    prev = prev_metrics or {}
    lcp_prev = prev.get("lcp_good_pct", "N/A")
    inp_prev = prev.get("inp_good_pct", "N/A")
    cls_prev = prev.get("cls_good_pct", "N/A")
    errors_prev = prev.get("index_error_count", "N/A")

    # Search Analytics
    perf = gsc_data.get("search_performance", {})
    current = perf.get("current", {})
    previous = perf.get("previous", {})
    top_queries = gsc_data.get("top_queries", [])[:10]
    top_pages = gsc_data.get("top_pages", [])[:10]

    queries_text = "\n".join(
        f"  {i+1}. \"{q['query']}\" — 클릭 {q['clicks']}, 노출 {q['impressions']}, CTR {q['ctr']}%, 순위 {q['position']}"
        for i, q in enumerate(top_queries)
    ) if top_queries else "  (데이터 없음)"

    pages_text = "\n".join(
        f"  {i+1}. {p['page']} — 클릭 {p['clicks']}, 노출 {p['impressions']}, CTR {p['ctr']}%, 순위 {p['position']}"
        for i, p in enumerate(top_pages)
    ) if top_pages else "  (데이터 없음)"

    return f"""[이번 주 크롤링 결과 - {today_str}]

## SiteOne 크롤링
{json.dumps(siteone_data, indent=2, ensure_ascii=False)}

## 검색 성과 (최근 7일)
- 총 클릭: {current.get('clicks', 0)} (전주 {previous.get('clicks', 'N/A')})
- 총 노출: {current.get('impressions', 0)} (전주 {previous.get('impressions', 'N/A')})
- 평균 CTR: {current.get('ctr', 0)}% (전주 {previous.get('ctr', 'N/A')}%)
- 평균 순위: {current.get('position', 0)} (전주 {previous.get('position', 'N/A')})

## TOP 검색어 (클릭 기준 상위 10개)
{queries_text}

## TOP 페이지 (클릭 기준 상위 10개)
{pages_text}

## GSC 데이터
- 사이트맵 제출 URL: {gsc_data.get('submitted_count', 0)}
- 검색 노출 페이지: {gsc_data.get('pages_with_impressions', 0)}
- CWV (Poor/Needs Improvement):
  LCP Poor: {lcp.get('poor_pct', 0)}%, NI: {lcp.get('needs_improvement_pct', 0)}%
  INP Poor: {inp.get('poor_pct', 0)}%, NI: {inp.get('needs_improvement_pct', 0)}%
  CLS Poor: {cls_.get('poor_pct', 0)}%, NI: {cls_.get('needs_improvement_pct', 0)}%
- 색인 오류 목록:
  {json.dumps(gsc_data.get('index_errors', []), indent=2, ensure_ascii=False)}

## 전체 지표 요약 (전주 대비)
- LCP Good: {lcp.get('good_pct', 0)}% (전주 {lcp_prev}%)
- INP Good: {inp.get('good_pct', 0)}% (전주 {inp_prev}%)
- CLS Good: {cls_.get('good_pct', 0)}% (전주 {cls_prev}%)
- 색인 오류: {len(gsc_data.get('index_errors', []))} (전주 {errors_prev})

[최근 2주 이슈 히스토리]
{json.dumps(history, indent=2, ensure_ascii=False)}

[관찰 중 항목]
{json.dumps(watch_items, indent=2, ensure_ascii=False)}

---
아래 JSON 형식으로만 응답하세요:

{{
  "summary": "전체 한 줄 요약",
  "new_issues": [
    {{
      "type": "이슈 유형",
      "severity": "Critical|Warning|Info",
      "url": "문제 URL (없으면 null)",
      "description": "이슈 설명",
      "reason": "우선순위 근거",
      "action": "조치 방향"
    }}
  ],
  "recurred_issues": [...],
  "resolved_issues": [
    {{
      "type": "",
      "url": "",
      "description": "해소 확인 내용"
    }}
  ],
  "watch_updates": [
    {{
      "type": "",
      "change": "수치 변화 설명",
      "comment": "급변 여부 판단 및 코멘트"
    }}
  ],
  "metric_comments": [
    {{
      "metric": "LCP|INP|CLS|색인|크롤링",
      "comment": "임계값 초과 시에만 작성"
    }}
  ],
  "skipped_count": 0
}}"""


def _parse_json_response(text: str) -> dict:
    """Claude 응답에서 JSON 추출."""
    # markdown code fence 제거 시도
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 전체 텍스트에서 JSON 객체 추출 시도
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            return json.loads(brace_match.group())
        raise ValueError(f"Failed to parse JSON from Claude response: {text[:200]}...")


def analyze_seo(
    siteone_data: dict,
    gsc_data: dict,
    history: list,
    watch_items: list,
    prev_metrics: dict | None,
) -> dict:
    """Claude API로 SEO 진단 수행."""
    client = anthropic.Anthropic(api_key=os.environ["CLAUDE_API_KEY"])

    user_prompt = _build_user_prompt(
        siteone_data, gsc_data, history, watch_items, prev_metrics
    )

    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    response_text = message.content[0].text
    result = _parse_json_response(response_text)

    # 토큰 사용량 메타데이터 첨부
    result["_token_usage"] = {
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
        "model": message.model,
    }

    # 필수 키 검증
    required_keys = [
        "summary", "new_issues", "recurred_issues",
        "resolved_issues", "watch_updates", "metric_comments", "skipped_count",
    ]
    for key in required_keys:
        if key not in result:
            result[key] = [] if key != "summary" and key != "skipped_count" else (
                "" if key == "summary" else 0
            )

    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    mock_siteone = {"error_urls": [], "slow_pages": []}
    mock_gsc = {
        "indexed_count": 100,
        "index_errors": [],
        "cwv": {
            "lcp": {"good_pct": 85, "needs_improvement_pct": 10, "poor_pct": 5, "p75": 2500},
            "inp": {"good_pct": 90, "needs_improvement_pct": 7, "poor_pct": 3, "p75": 200},
            "cls": {"good_pct": 95, "needs_improvement_pct": 3, "poor_pct": 2, "p75": 0.1},
        },
    }
    result = analyze_seo(mock_siteone, mock_gsc, [], [], None)
    print(json.dumps(result, indent=2, ensure_ascii=False))
