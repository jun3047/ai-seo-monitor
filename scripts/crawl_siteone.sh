#!/usr/bin/env bash
set -euo pipefail

if [ -z "${SITE_URL:-}" ]; then
  echo "ERROR: SITE_URL environment variable is not set"
  exit 1
fi

mkdir -p ./tmp

echo "Starting SiteOne crawl for: $SITE_URL"

siteone-crawler \
  --url="$SITE_URL" \
  --output=json \
  --output-json-file=./tmp/siteone-report.json \
  --max-reqs-per-sec=10 \
  --workers=3

echo "SiteOne crawl completed. Output: ./tmp/siteone-report.json"
