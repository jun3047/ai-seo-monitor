# AI SEO Monitor

매주 월요일 자동으로 웹사이트 SEO 상태를 점검하고, Claude AI가 진단한 결과를 Notion에 기록하고 Slack으로 리포트를 발송하는 시스템입니다.

## 파이프라인

```
SiteOne 크롤링 → GSC/CrUX 수집 → Notion 히스토리 조회 → Claude 진단 → Notion 업데이트 → Slack 발송
```

| 단계 | 스크립트 | 설명 |
|------|----------|------|
| 1 | `crawl_siteone.sh` | SiteOne CLI로 사이트 크롤링, JSON 출력 |
| 2 | `fetch_gsc.py` | GSC API(색인 현황) + CrUX API(Core Web Vitals) 수집 |
| 3-4 | `fetch_notion.py` | 최근 2주 이슈 히스토리 + 관찰 중 항목 + 전주 리포트 조회 |
| 5 | `analyze_claude.py` | Claude API로 수집 데이터 종합 진단, JSON 응답 |
| 6 | `sync_notion.py` | 이슈 히스토리 DB 업데이트 + 주간 리포트 DB 기록 |
| 7 | `send_slack.py` | Slack 메인 메시지 + 스레드 상세 리포트 발송 |

`main.py`가 전체 파이프라인을 오케스트레이션하며, 각 단계의 실행시간/토큰 사용량 등 디버그 로그를 `tmp/debug_log.json`에 기록합니다.

## 실행 방식

- **자동**: GitHub Actions 스케줄 (매주 월요일 KST 10:00)
- **수동**: GitHub Actions > `workflow_dispatch` (dry_run 옵션 지원)
- **로컬**: `.env` 파일 설정 후 아래 명령 실행

```bash
pip install -r requirements.txt
bash scripts/crawl_siteone.sh
python scripts/main.py
```

## 세팅 방법

### 1. Notion 데이터베이스 생성

Notion에 아래 3개 데이터베이스를 생성하고, 각 DB ID를 환경변수에 설정합니다.

#### 이슈 히스토리 DB (`NOTION_ISSUE_DB_ID`)

| 속성 이름 | 타입 | 비고 |
|-----------|------|------|
| 이슈 유형 | Select | |
| URL | URL | |
| 상태 | Select | 옵션: `신규`, `미해소`, `재발`, `해소` |
| 심각도 | Select | 옵션: `Critical`, `Warning`, `Info` |
| 이슈 설명 | Text | |
| 조치 방향 | Text | |
| 이슈 지문 hash | Text | 자동 생성 (MD5) |
| 최초 발견일 | Date | |
| 최종 확인일 | Date | 매주 자동 갱신 |
| 해소일 | Date | |

#### 주간 리포트 DB (`NOTION_REPORT_DB_ID`)

| 속성 이름 | 타입 | 비고 |
|-----------|------|------|
| 리포트 날짜 | Date | |
| Claude 요약 | Text | |
| 신규 이슈 수 | Number | |
| 재발 이슈 수 | Number | |
| 해소 이슈 수 | Number | |
| 생략 이슈 수 | Number | |
| LCP Good % | Number | |
| INP Good % | Number | |
| CLS Good % | Number | |
| 색인 수 | Number | |
| 색인 오류 수 | Number | |
| 평균 응답시간 | Number | ms 단위 |
| Notion 이슈 링크 | URL | |

#### 관찰 중 항목 DB (`NOTION_WATCH_DB_ID`)

| 속성 이름 | 타입 | 비고 |
|-----------|------|------|
| 이슈 유형 | Select | |
| 배경 설명 | Text | |
| 상태 | Select | 옵션: `관찰 중`, `해소` |
| 등록일 | Date | |

### 2. Notion Integration 생성

1. [Notion Integrations](https://www.notion.so/my-integrations) 에서 새 Integration 생성
2. 생성된 토큰을 `NOTION_TOKEN`에 설정
3. 위 3개 DB 각각에서 우측 상단 `...` > `연결` > 생성한 Integration 추가

### 3. Google 서비스 설정

#### GSC (Google Search Console)

1. [Google Cloud Console](https://console.cloud.google.com/)에서 서비스 계정 생성
2. **Search Console API** + **Chrome UX Report API** 활성화
3. 서비스 계정 JSON 키 다운로드 → 내용을 `GSC_SERVICE_ACCOUNT_JSON`에 설정
4. GSC에서 해당 서비스 계정 이메일을 사이트 사용자로 추가

### 4. Slack Bot 설정

1. [Slack API](https://api.slack.com/apps) 에서 새 App 생성
2. OAuth & Permissions에서 `chat:write` 스코프 추가
3. Bot Token (`xoxb-...`)을 `SLACK_BOT_TOKEN`에 설정
4. 리포트 발송할 채널 ID를 `SLACK_CHANNEL_ID`에 설정
5. 해당 채널에 Bot을 초대

### 5. 환경변수 설정

GitHub Actions: Repository Settings > Secrets and variables > Actions에 아래 시크릿 등록

| 변수 | 설명 | 필수 |
|------|------|------|
| `SITE_URL` | 크롤링 대상 URL (예: `https://example.com`) | O |
| `CLAUDE_API_KEY` | Anthropic Claude API 키 | O |
| `GSC_SERVICE_ACCOUNT_JSON` | GSC + CrUX 서비스 계정 JSON 문자열 | O |
| `SLACK_BOT_TOKEN` | Slack Bot Token (`xoxb-...`) | O |
| `SLACK_CHANNEL_ID` | Slack 채널 ID | O |
| `NOTION_TOKEN` | Notion Integration Token | O |
| `NOTION_ISSUE_DB_ID` | 이슈 히스토리 DB ID | O |
| `NOTION_REPORT_DB_ID` | 주간 리포트 DB ID | O |
| `NOTION_WATCH_DB_ID` | 관찰 중 항목 DB ID | O |

로컬 실행 시 프로젝트 루트에 `.env` 파일을 생성하여 동일한 변수를 설정합니다.

## 디버그 로그

매 실행마다 `tmp/debug_log.json`에 아래 정보가 기록됩니다:

- 각 스텝별 실행시간 (초)
- Claude API 토큰 사용량 (input/output)
- 이슈 건수 요약 (신규/재발/해소/생략)
- 에러 목록
- 전체 파이프라인 소요시간

## 기술 스택

- **실행 환경**: GitHub Actions (ubuntu-latest)
- **크롤러**: SiteOne CLI
- **언어**: Python 3.11
- **AI**: Claude API (claude-sonnet-4-5-20251001)
- **데이터**: Notion API, Google Search Console API, CrUX API
- **알림**: Slack API
