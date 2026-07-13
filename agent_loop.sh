#!/usr/bin/env bash
set -euo pipefail

# ─── Config ───────────────────────────────────────────────────────────────────
TASK="${1:-Fix bugs, improve code quality, and make tests pass}"
MODEL="${MODEL:-qwen2.5-coder:14b}"
MAX_LOOPS="${MAX_LOOPS:-5}"
AUTO_APPLY="${AUTO_APPLY:-1}"          # 1=apply patches automatically, 0=pause for review
BACKUP_DIR=".agent_backups"
TEST_OUTPUT=".agent_test_output.txt"
RESPONSE_FILE=".agent_response.txt"
PATCH_LOG=".agent_patches.log"

# Remote API support: set AGENT_API=openai|anthropic and AGENT_API_KEY
AGENT_API="${AGENT_API:-ollama}"
AGENT_API_KEY="${AGENT_API_KEY:-}"
OPENAI_BASE="${OPENAI_BASE:-https://api.openai.com/v1}"

# ─── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${CYAN}[agent]${RESET} $*"; }
ok()   { echo -e "${GREEN}[pass]${RESET}  $*"; }
warn() { echo -e "${YELLOW}[warn]${RESET}  $*"; }
fail() { echo -e "${RED}[fail]${RESET}  $*"; }
hr()   { echo -e "${BOLD}$(printf '─%.0s' {1..60})${RESET}"; }

# ─── Detect test command ──────────────────────────────────────────────────────
detect_test_cmd() {
    if command -v pytest &>/dev/null && \
       { [ -f "pytest.ini" ] || [ -f "pyproject.toml" ] || \
         [ -f "setup.cfg" ] || [ -d "tests" ] || [ -d "test" ]; }; then
        echo "pytest -q"
    elif [ -f "package.json" ] && command -v npm &>/dev/null; then
        echo "npm test"
    elif [ -f "go.mod" ] && command -v go &>/dev/null; then
        echo "go test ./..."
    elif [ -f "Cargo.toml" ] && command -v cargo &>/dev/null; then
        echo "cargo test"
    elif command -v pytest &>/dev/null; then
        echo "pytest -q"
    else
        echo ""
    fi
}

run_tests() {
    local cmd="$1"
    log "Running: $cmd"
    if $cmd >"$TEST_OUTPUT" 2>&1; then
        return 0
    else
        return 1
    fi
}

# ─── LLM call (ollama local or remote API) ───────────────────────────────────
call_model() {
    local prompt="$1"

    case "$AGENT_API" in
    openai)
        [ -z "$AGENT_API_KEY" ] && { fail "AGENT_API_KEY not set for openai"; exit 1; }
        local payload
        payload="$(python3 -c "
import json, sys
prompt = open('/dev/stdin').read()
print(json.dumps({'model': '${MODEL}', 'messages': [{'role':'user','content':prompt}], 'temperature':0.2}))
" <<< "$prompt")"
        curl -fsSL -X POST "${OPENAI_BASE}/chat/completions" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${AGENT_API_KEY}" \
            -d "$payload" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])"
        ;;
    anthropic)
        [ -z "$AGENT_API_KEY" ] && { fail "AGENT_API_KEY not set for anthropic"; exit 1; }
        local payload
        payload="$(python3 -c "
import json, sys
prompt = open('/dev/stdin').read()
print(json.dumps({'model':'${MODEL}','max_tokens':4096,'messages':[{'role':'user','content':prompt}]}))
" <<< "$prompt")"
        curl -fsSL -X POST "https://api.anthropic.com/v1/messages" \
            -H "Content-Type: application/json" \
            -H "x-api-key: ${AGENT_API_KEY}" \
            -H "anthropic-version: 2023-06-01" \
            -d "$payload" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['content'][0]['text'])"
        ;;
    *)
        # Default: local Ollama
        ollama run "$MODEL" "$prompt"
        ;;
    esac
}

# ─── Auto-apply FILE: blocks from model response ─────────────────────────────
# Parses:
#   FILE: path/to/file.ext
#   ```
#   <content>
#   ```
apply_patches() {
    local response_file="$1"
    local applied=0

    mkdir -p "$BACKUP_DIR"
    echo "=== Loop $LOOP patches $(date) ===" >> "$PATCH_LOG"

    python3 - "$response_file" "$BACKUP_DIR" "$PATCH_LOG" <<'PYEOF'
import sys, re, os, shutil, datetime

response_file = sys.argv[1]
backup_dir    = sys.argv[2]
patch_log     = sys.argv[3]

text = open(response_file).read()

# Match: FILE: some/path.ext  followed by a fenced code block
pattern = re.compile(
    r'FILE:\s*(\S+)\n```[^\n]*\n(.*?)```',
    re.DOTALL
)

matches = pattern.findall(text)
count = 0
for filepath, content in matches:
    filepath = filepath.strip()
    # Safety: no absolute paths, no traversal
    if os.path.isabs(filepath) or '..' in filepath.split(os.sep):
        print(f"[agent] SKIP unsafe path: {filepath}")
        continue
    # Backup original
    if os.path.exists(filepath):
        ts = datetime.datetime.now().strftime('%H%M%S')
        bname = filepath.replace('/', '_') + f'.bak{ts}'
        shutil.copy2(filepath, os.path.join(backup_dir, bname))
        print(f"[agent] Backed up: {filepath} → {backup_dir}/{bname}")
    # Write new content
    os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else '.', exist_ok=True)
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"[agent] Applied:  {filepath}")
    with open(patch_log, 'a') as pf:
        pf.write(f"  WROTE: {filepath}\n")
    count += 1

if count == 0:
    print("[agent] No FILE: blocks found in response — nothing applied.")
else:
    print(f"[agent] Applied {count} file(s).")
PYEOF
}

# ─── Build prompt ─────────────────────────────────────────────────────────────
build_prompt() {
    local test_out="$1"
    local diff_out="$2"
    cat <<PROMPT
You are a senior software engineer fixing a codebase autonomously.

TASK:
$TASK

GIT DIFF (current uncommitted changes):
${diff_out:-"(none)"}

TEST OUTPUT (failures to fix):
$test_out

Instructions:
- Identify the root cause of each test failure.
- Output ONLY the files that need to change.
- For each changed file use EXACTLY this format (no extra text between):

FILE: path/to/file.ext
\`\`\`
<complete corrected file content>
\`\`\`

- Fix all failing tests. Do not break passing ones.
- Do not add unneeded dependencies.
- Keep changes minimal and correct.
- If all tests already pass, output exactly: TESTS PASSING — no changes needed.
PROMPT
}

# ─── Preflight ────────────────────────────────────────────────────────────────
hr
echo -e "${BOLD}  Agent Loop  [AUTO_APPLY=${AUTO_APPLY}]${RESET}"
hr
log "Task:       $TASK"
log "Model:      $MODEL  (via $AGENT_API)"
log "Max loops:  $MAX_LOOPS"
log "Auto-apply: $AUTO_APPLY"

if [ "$AGENT_API" = "ollama" ]; then
    if ! command -v ollama &>/dev/null; then
        fail "ollama not found. Install from https://ollama.com"
        exit 1
    fi
fi

if ! command -v python3 &>/dev/null; then
    fail "python3 required for patch parsing"
    exit 1
fi

TEST_CMD="$(detect_test_cmd)"
if [ -z "$TEST_CMD" ]; then
    fail "No supported test runner found (pytest / npm test / go test / cargo test)."
    exit 1
fi
log "Test cmd:   $TEST_CMD"
hr

# ─── Main loop ────────────────────────────────────────────────────────────────
LOOP=0
while [ "$LOOP" -lt "$MAX_LOOPS" ]; do
    LOOP=$(( LOOP + 1 ))
    echo
    echo -e "${BOLD}━━━  Loop $LOOP / $MAX_LOOPS  ━━━${RESET}"
    hr

    # 1. Run tests
    if run_tests "$TEST_CMD"; then
        ok "Tests passed!"
        hr
        ok "All tests pass — agent loop complete after $LOOP loop(s)."
        exit 0
    fi

    fail "Tests failed."
    echo
    echo "── Test output ──────────────────────────"
    cat "$TEST_OUTPUT"
    echo "─────────────────────────────────────────"
    echo

    # Early-exit if last loop (don't waste a model call)
    if [ "$LOOP" -ge "$MAX_LOOPS" ]; then
        break
    fi

    # 2. Gather context
    DIFF_OUT="$(git diff 2>/dev/null || echo '(not a git repo or no changes)')"
    TEST_OUT="$(cat "$TEST_OUTPUT")"
    PROMPT="$(build_prompt "$TEST_OUT" "$DIFF_OUT")"

    # 3. Call model
    log "Calling $MODEL via $AGENT_API …"
    echo
    call_model "$PROMPT" | tee "$RESPONSE_FILE"
    echo
    hr
    log "Response saved → $RESPONSE_FILE"

    # 4. Check for early-success signal
    if grep -qi "TESTS PASSING" "$RESPONSE_FILE"; then
        ok "Model reports tests are passing — verifying …"
    fi

    # 5. Apply or pause
    if [ "$AUTO_APPLY" = "1" ]; then
        echo
        log "Auto-applying patches …"
        apply_patches "$RESPONSE_FILE"
    else
        echo
        warn "AUTO_APPLY=0 — apply changes manually, then press ENTER to continue (Ctrl-C to abort)."
        read -r
    fi

    echo
done

# Final test run
echo
echo -e "${BOLD}━━━  Final test run  ━━━${RESET}"
hr
if run_tests "$TEST_CMD"; then
    ok "Tests passed on final check!"
    exit 0
fi

echo
fail "MAX_LOOPS ($MAX_LOOPS) reached — tests still failing."
log "Last response:    $RESPONSE_FILE"
log "Last test output: $TEST_OUTPUT"
log "Patch log:        $PATCH_LOG"
log "Backups:          $BACKUP_DIR/"
exit 1

# ─── Usage examples ───────────────────────────────────────────────────────────
#
# Local Ollama, fully autonomous (default):
#   bash agent_loop.sh
#
# Custom task:
#   bash agent_loop.sh "Fix the broken auth tests in tests/test_auth.py"
#
# Manual review mode (no auto-apply):
#   AUTO_APPLY=0 bash agent_loop.sh
#
# Different local model:
#   MODEL=deepseek-coder:6.7b bash agent_loop.sh
#
# More loops:
#   MAX_LOOPS=10 bash agent_loop.sh "Refactor the DB layer and fix tests"
#
# Remote: OpenAI-compatible API (e.g., Together, Groq, OpenRouter):
#   AGENT_API=openai MODEL=gpt-4o AGENT_API_KEY=sk-... bash agent_loop.sh
#   AGENT_API=openai MODEL=meta-llama/Llama-3-70b-chat-hf \
#     OPENAI_BASE=https://api.together.xyz/v1 AGENT_API_KEY=... bash agent_loop.sh
#
# Remote: Anthropic Claude:
#   AGENT_API=anthropic MODEL=claude-opus-4-8 AGENT_API_KEY=sk-ant-... bash agent_loop.sh
#
# Remote server (SSH in, then run):
#   scp agent_loop.sh user@myserver:~/project/
#   ssh user@myserver "cd ~/project && bash agent_loop.sh"
