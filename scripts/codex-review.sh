#!/usr/bin/env bash
# Advisory Codex review wrapper.
# Always exits 0. Never blocks the caller.

set -u

TIMEOUT=120
RAW=0
PROMPT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --timeout)
      TIMEOUT="${2:-120}"
      shift 2
      ;;
    --raw)
      RAW=1
      shift
      ;;
    --)
      shift
      PROMPT="$*"
      break
      ;;
    *)
      PROMPT="${PROMPT} $1"
      shift
      ;;
  esac
done

emit() {
  local status="$1"
  local reason="$2"
  local body="${3:-}"

  if [[ "$RAW" -eq 1 ]]; then
    echo "## [$status] $reason"
    if [[ -n "$body" ]]; then
      echo
      printf "%s\n" "$body"
    fi
  else
    python3 - "$status" "$reason" "$body" <<'PY'
import json
import sys

print(json.dumps({
    "status": sys.argv[1],
    "reason": sys.argv[2],
    "body": sys.argv[3],
}, ensure_ascii=False))
PY
  fi
}

if ! command -v codex >/dev/null 2>&1; then
  emit "SKIPPED" "codex CLI not installed"
  exit 0
fi

if [[ -z "$PROMPT" ]]; then
  PROMPT="Review the current repository changes as an advisory reviewer. Do not modify files."
fi

collect_file() {
  local file="$1"

  if [[ -f "$file" ]]; then
    echo
    echo "===== FILE: $file ====="
    sed -n '1,600p' "$file"
  fi
}

# Files always included as project rule context.
BASELINE_FILES=(
  "CLAUDE.md"
  "AGENTS.md"
  ".claude/commands/ship.md"
)

# Files actually changed in the working tree (modified + staged + untracked).
# Excludes generated/transient paths so the prompt stays signal-rich.
declare -a CHANGED_FILES=()
while IFS= read -r line; do
  [[ -n "$line" ]] && CHANGED_FILES+=("$line")
done < <(
  {
    git diff --name-only 2>/dev/null || true
    git diff --name-only --cached 2>/dev/null || true
    git ls-files --others --exclude-standard 2>/dev/null || true
  } \
    | grep -E -v '^(reports/|.*__pycache__/|.*\.venv/)' \
    | sort -u
)

# Build collect order: baseline first, then changed files (deduped).
declare -a FILES_TO_COLLECT=()
for f in "${BASELINE_FILES[@]}"; do
  FILES_TO_COLLECT+=("$f")
done
if [[ ${#CHANGED_FILES[@]} -gt 0 ]]; then
  for f in "${CHANGED_FILES[@]}"; do
    skip=0
    for b in "${BASELINE_FILES[@]}"; do
      if [[ "$f" == "$b" ]]; then skip=1; break; fi
    done
    [[ "$skip" -eq 0 ]] && FILES_TO_COLLECT+=("$f")
  done
fi

CONTEXT="$(
  echo "===== GIT STATUS ====="
  git status --short 2>/dev/null || true

  echo
  echo "===== AUTO-COLLECTED FILES ====="
  printf '%s\n' "${FILES_TO_COLLECT[@]}"

  for f in "${FILES_TO_COLLECT[@]}"; do
    collect_file "$f"
  done
)"

REVIEW_PROMPT=$(cat <<PROMPT_EOF
You are an advisory reviewer.

Repository context:
- Kubernetes-based LLM service operations diagnosis platform
- MVP pipeline: k6 -> mock LLM -> Prometheus -> analyzer -> Markdown/JSON report
- Claude Code is the implementer
- Codex is only an advisory reviewer
- Do not modify files
- Do not block progress

User review request:
$PROMPT

Repository files and contents:
$CONTEXT

Return review findings in this format:

[CRITICAL] file:line - issue
Why it matters:
Suggested fix:

[WARNING] file:line - issue
Why it matters:
Suggested fix:

[INFO] file:line - note

Only report real issues.
Do not nitpick formatting.
PROMPT_EOF
)

OUT_FILE="$(mktemp)"
trap 'rm -f "$OUT_FILE"' EXIT

echo "[codex-review] running advisory review..." >&2

if command -v timeout >/dev/null 2>&1; then
  timeout "$TIMEOUT" codex exec "$REVIEW_PROMPT" > "$OUT_FILE" 2>&1
  CODE=$?
else
  codex exec "$REVIEW_PROMPT" > "$OUT_FILE" 2>&1
  CODE=$?
fi

BODY="$(cat "$OUT_FILE")"

if [[ "$CODE" -eq 0 ]]; then
  emit "OK" "advisory review completed" "$BODY"
elif [[ "$CODE" -eq 124 ]]; then
  emit "TIMEOUT" "codex timed out after ${TIMEOUT}s" "$BODY"
else
  emit "ERROR" "codex exited with code ${CODE}" "$BODY"
fi

exit 0
