#!/usr/bin/env bash
# 一键跑 PLTP 样例 + 黄金用例。
set -euo pipefail

cd "$(dirname "$0")/.."

PY=".venv/Scripts/python.exe"
if [ ! -x "$PY" ]; then
  PY="python"
fi

if [ ! -f "data/samples/PDF 示例账单2.pdf" ]; then
  echo "缺少 data/samples/。请先从题目提供的网盘下载样例，放到 data/samples/。"
  echo "合成 Excel/Word 在 tests/fixtures/synthetic/，不需要下载。"
  exit 1
fi

echo "== 黄金用例 =="
"$PY" -m pytest tests/test_golden.py -q

echo "== PDF2 + exact 种子（NO_CHANGE）=="
"$PY" scripts/seed_existing_bills.py --variant exact --reset
"$PY" -m shipping_agent.cli decide \
  --input "data/samples/PDF 示例账单2.pdf" \
  --no-ocr --no-llm \
  --out data/output/run_pdf2_nochange.json

echo "== PDF2 + update 种子（UPDATE / NO_CHANGE）=="
"$PY" scripts/seed_existing_bills.py --variant update --reset
"$PY" -m shipping_agent.cli decide \
  --input "data/samples/PDF 示例账单2.pdf" \
  --no-ocr --no-llm \
  --out data/output/run_pdf2_update.json

echo "== PDF1（AR/AP 不明 -> PENDING_REVIEW）=="
"$PY" -m shipping_agent.cli decide \
  --input "data/samples/PDF示例账单1.pdf" \
  --no-ocr --no-llm \
  --out data/output/run_pdf1_pending.json

echo "== EML（内部邮件 -> PENDING_REVIEW）=="
"$PY" -m shipping_agent.cli decide \
  --input data/samples/*.eml \
  --no-ocr --no-llm \
  --out data/output/run_eml_pending.json

echo "== Excel / Word 合成样例 =="
"$PY" -m shipping_agent.cli decide \
  --input tests/fixtures/synthetic/charges.xlsx \
  --input tests/fixtures/synthetic/debit_note.docx \
  --no-ocr --no-llm \
  --out data/output/run_synthetic.json

echo
echo "全部完成。输出在 data/output/run_*.json"
