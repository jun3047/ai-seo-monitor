## Claude Code 전달용 구현 명세서

### 프로젝트 개요
매주 월요일 자동으로 SEO 모니터링을 수행하고, AI 진단 후 Notion에 기록 및 Slack으로 리포트를 발송하는 시스템

---

### 기술 스택
- 실행 환경: GitHub Actions (ubuntu-latest)
- 크롤러: SiteOne CLI (apt 설치, 무료 오픈소스)
- 언어: Python 3.11
- AI: Claude API (claude-sonnet-4-5-20251001)
- 저장소: Notion API
- 알림: Slack API (Bot Token, 스레드 방식)

---

### 파일 구조

```
.github/
  workflows/
    seo-monitor.yml
scripts/
  crawl_siteone.sh
  fetch_gsc.py
  fetch_notion.py
  analyze_claude.py
  sync_notion.py
  send_slack.py
  main.py
requirements.txt
```

---

### GitHub Actions 워크플로우 (`seo-monitor.yml`)

```yaml
name: SEO Weekly Monitor
on:
  schedule:
    - cron: '0 1 * * 1'  # 매주 월요일 KST 10:00
  workflow_dispatch:       # 수동 실행 가능

jobs:
  seo-monitor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Install SiteOne CLI
        run: |
          curl -1sLf \
            'https://dl.cloudsmith.io/public/janreges/siteone-crawler/setup.deb.sh' \
            | sudo -E bash
          sudo apt-get install siteone-crawler

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run SEO Monitor
        run: python scripts/main.py
        env:
          SITE_URL: ${{ secrets.SITE_URL }}
          CLAUDE_API_KEY: ${{ secrets.CLAUDE_API_KEY }}
          GSC_SERVICE_ACCOUNT_JSON: ${{ secrets.GSC_SERVICE_ACCOUNT_JSON }}
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          SLACK_CHANNEL_ID: ${{ secrets.SLACK_CHANNEL_ID }}
          NOTION_TOKEN: ${{ secrets.NOTION_TOKEN }}
          NOTION_ISSUE_DB_ID: ${{ secrets.NOTION_ISSUE_DB_ID }}
          NOTION_REPORT_DB_ID: ${{ secrets.NOTION_REPORT_DB_ID }}
          NOTION_WATCH_DB_ID: ${{ secrets.NOTION_WATCH_DB_ID }}
```

---

### 전체 파이프라인 (`main.py`)

```
실행 순서:
1. SiteOne CLI 크롤링 실행 → JSON 저장
2. GSC API 데이터 수집
3. Notion에서 최근 2주 이슈 히스토리 조회
4. Notion에서 관찰 중 항목 조회
5. Claude API로 진단 (수집데이터 + 히스토리 + 관찰항목 전달)
6. Notion 이슈 히스토리 업데이트
7. Notion 주간 리포트 기록
8. Slack 스레드 발송
```

---

### 1. SiteOne 크롤링 (`crawl_siteone.sh`)

```bash
siteone-crawler \
  --url=$SITE_URL \
  --output-type=json \
  --output-file=./tmp/siteone-report.json \
  --max-reqs-per-sec=10 \
  --workers=3
```

**추출할 항목 (JSON에서 파싱):**
- 4xx/5xx 상태코드 URL 목록
- 리다이렉트 체인/루프 URL 목록
- title 누락/중복 페이지
- meta description 누락/중복 페이지
- H1 누락/중복 페이지
- canonical 태그 이슈
- 이미지 alt 누락
- 페이지별 응답시간 (느린 페이지 TOP 5, 기준: 1s 초과)
- robots.txt/noindex 감지

---

### 2. GSC 데이터 수집 (`fetch_gsc.py`)

**사용 API:**
- Google Search Console API v3
- 인증: 서비스 계정 JSON (환경변수로 주입)

**수집 항목:**

```python
# 색인 현황
- 색인된 페이지 수 (전주 대비)
- 색인 오류 페이지 수 및 목록 (유형별: noindex, 크롤링 차단, 404 등)

# CWV - Poor/Needs Improvement 페이지만 수집 (Good은 제외)
- LCP: 페이지별 수치 + 등급
- INP: 페이지별 수치 + 등급
- CLS: 페이지별 수치 + 등급

# 크롤링 통계 (페이지별)
- Googlebot 응답시간 임계값 초과 페이지 (기준: 1s 초과)
- 전체 크롤링 요청 수

# 전체 요약 수치 (트렌드용)
- LCP Good 비율 %
- INP Good 비율 %
- CLS Good 비율 %
- 색인 수, 오류 수
```

---

### 3. Notion 히스토리 조회 (`fetch_notion.py`)

**조회 대상 DB 2개:**

```
① 이슈 히스토리 DB
  - 필터: 최근 2주 이내 최종 확인일
  - 반환: 이슈 지문(hash), 유형, URL, 상태, 최초 발견일, 최종 확인일

② 관찰 중 항목 DB
  - 필터: 상태 = '관찰 중'
  - 반환: 이슈 유형, 배경 설명, 등록일
```

---

### 4. Claude API 진단 (`analyze_claude.py`)

**System Prompt:**
```
당신은 프론트엔드 팀의 SEO 모니터링 전문가입니다.
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
- 응답은 반드시 JSON으로만 반환
```

**User Prompt:**
```
[이번 주 크롤링 결과 - {날짜}]

## SiteOne 크롤링
{siteone_filtered_json}

## GSC 데이터
- CWV 이슈 페이지 (Poor/Needs Improvement만):
  {gsc_cwv_issues}
- Googlebot 응답 느린 페이지 (1s 초과):
  {gsc_slow_pages}
- 색인 오류 목록:
  {gsc_index_errors}

## 전체 지표 요약 (전주 대비)
- LCP Good: {lcp_good}% (전주 {lcp_good_prev}%)
- INP Good: {inp_good}% (전주 {inp_good_prev}%)
- CLS Good: {cls_good}% (전주 {cls_good_prev}%)
- 색인 수: {indexed} (전주 {indexed_prev})
- 색인 오류: {index_errors} (전주 {index_errors_prev})

[최근 2주 이슈 히스토리]
{notion_history_json}

[관찰 중 항목]
{notion_watch_json}

---
아래 JSON 형식으로만 응답하세요:

{
  "summary": "전체 한 줄 요약",
  "new_issues": [
    {
      "type": "이슈 유형",
      "severity": "Critical|Warning|Info",
      "url": "문제 URL (없으면 null)",
      "description": "이슈 설명",
      "reason": "우선순위 근거",
      "action": "조치 방향"
    }
  ],
  "recurred_issues": [...],
  "resolved_issues": [
    {
      "type": "",
      "url": "",
      "description": "해소 확인 내용"
    }
  ],
  "watch_updates": [
    {
      "type": "",
      "change": "수치 변화 설명",
      "comment": "급변 여부 판단 및 코멘트"
    }
  ],
  "metric_comments": [
    {
      "metric": "LCP|INP|CLS|색인|크롤링",
      "comment": "임계값 초과 시에만 작성"
    }
  ],
  "skipped_count": 0
}
```

---

### 5. Notion 업데이트 (`sync_notion.py`)

**이슈 히스토리 DB 컬럼:**
```
- 이슈 유형 (select)
- URL (url)
- 상태 (select): 신규 | 미해소 | 재발 | 해소
- 심각도 (select): Critical | Warning | Info
- 이슈 설명 (text)
- 조치 방향 (text)
- 이슈 지문 hash (text): f"{type}:{url}:{description}" MD5
- 최초 발견일 (date)
- 최종 확인일 (date): 매주 갱신
- 해소일 (date)
```

**업데이트 로직:**
```python
# 신규 이슈 → 새 row 생성, 상태 = '신규'
# 재발 이슈 → 기존 row 상태 = '재발', 최종확인일 갱신
# 해소 이슈 → 기존 row 상태 = '해소', 해소일 기록
# 미해소 이슈 → 최종확인일만 갱신
```

**주간 리포트 DB 컬럼:**
```
- 리포트 날짜 (date)
- Claude 요약 (text)
- 신규 이슈 수 (number)
- 재발 이슈 수 (number)
- 해소 이슈 수 (number)
- 생략 이슈 수 (number)
- LCP Good % (number)
- INP Good % (number)
- CLS Good % (number)
- 색인 수 (number)
- 색인 오류 수 (number)
- 평균 응답시간 (number)
- Notion 이슈 링크 (url)
```

---

### 6. Slack 발송 (`send_slack.py`)

**방식:** Slack API Bot Token (`chat.postMessage`)

**Step 1 - 메인 메시지 발송:**
```
[주간 타임스프레드 SEO 확인] {날짜}
```

**Step 2 - 해당 메시지 thread_ts로 스레드에 상세 리포트 댓글:**
```
📊 SEO 주간 모니터링 | {날짜}

💬 Claude 진단
{summary}

━━━━━━━━━━━━━━━━
📈 주요 지표
━━━━━━━━━━━━━━━━
🗂 색인: {indexed}개 (전주 대비 {indexed_diff})
⚠️ 색인 오류: {index_errors}건 (전주 대비 {errors_diff})

⚡ Core Web Vitals
• LCP  Good {lcp}%
• INP  Good {inp}%
• CLS  Good {cls}%

{metric_comments}  ← 임계값 초과 항목만 표시

━━━━━━━━━━━━━━━━
🔴 신규 이슈 ({count}건)
• [{severity}] {url} - {description}
  └ {action}

🟡 재발 이슈 ({count}건)
• [{severity}] {url} - {description}

✅ 해소된 이슈 ({count}건)
• {url} - {description}

👀 관찰 중 항목
• {type}: {change} - {comment}

📎 Notion 상세 보고서: {링크}
(기존 미해소 이슈 {skipped_count}건 생략)
```

---

### requirements.txt

```
anthropic
google-auth
google-auth-httplib2
google-api-python-client
notion-client
slack-sdk
python-dotenv
```

---

### 환경변수 목록

```
SITE_URL                   # 크롤링 대상 URL
CLAUDE_API_KEY             # Claude API 키
GSC_SERVICE_ACCOUNT_JSON   # GSC 서비스 계정 JSON (문자열)
SLACK_BOT_TOKEN            # xoxb-... 형태
SLACK_CHANNEL_ID           # 발송 채널 ID
NOTION_TOKEN               # Notion Integration Token
NOTION_ISSUE_DB_ID         # 이슈 히스토리 DB ID
NOTION_REPORT_DB_ID        # 주간 리포트 DB ID
NOTION_WATCH_DB_ID         # 관찰 중 항목 DB ID
```
