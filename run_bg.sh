#!/bin/bash
# Chạy Claude Code skill ở nền, gập máy vẫn tiếp (cần cắm sạc)
# Dùng: ./run_bg.sh <tên-skill> [tham số...]
#
# Ví dụ:
#   ./run_bg.sh seo-content-review "https://docs.google.com/..."
#   ./run_bg.sh branding-check "https://docs.google.com/..." SEONGON

SKILL="$1"
shift
ARGS="$@"

if [ -z "$SKILL" ]; then
  echo "Dùng: ./run_bg.sh <tên-skill> [tham số...]"
  echo ""
  echo "Skills có sẵn:"
  echo "  seo-content-review"
  echo "  branding-check"
  exit 1
fi

SKILL_DIR="$HOME/.claude/skills"
SKILL_PATH=""

# Tìm skill script
if [ -d "$SKILL_DIR/seo-content-reviewer" ] && [ "$SKILL" = "seo-content-review" ]; then
  SKILL_PATH="$SKILL_DIR/seo-content-reviewer/skill.py"
elif [ -d "$SKILL_DIR/branding-checker" ] && [ "$SKILL" = "branding-check" ]; then
  SKILL_PATH="$SKILL_DIR/branding-checker/skill.py"
else
  echo "Không tìm thấy skill: $SKILL"
  exit 1
fi

LOG_FILE="$(pwd)/logs/${SKILL}_$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$(pwd)/logs"

echo "▶ Chạy nền: $SKILL"
echo "  Args : $ARGS"
echo "  Log  : $LOG_FILE"
echo "  (Cắm sạc để gập máy vẫn chạy tiếp)"
echo ""

# caffeinate -s giữ máy không ngủ khi cắm sạc
# nohup tách khỏi terminal hiện tại
nohup caffeinate -si python3 "$SKILL_PATH" $ARGS > "$LOG_FILE" 2>&1 &

PID=$!
echo $PID > "$(pwd)/logs/${SKILL}_last.pid"

echo "✅ Đã chạy nền (PID: $PID)"
echo ""
echo "Theo dõi tiến độ:"
echo "  tail -f $LOG_FILE"
echo ""
echo "Dừng khi cần:"
echo "  kill $PID"
