#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CODE_DIR="${REPO_ROOT}/code"
TMP_DIR="$(mktemp -d "/tmp/s8-submission.XXXXXX")"

cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

section() {
  local title="$1"
  printf '\n'
  printf '===============================================================================\n'
  printf '%s\n' "${title}"
  printf '===============================================================================\n'
}

subsection() {
  local title="$1"
  printf '\n--- %s ---\n' "${title}"
}

print_run_header() {
  local label="$1"
  local query="$2"
  section "${label}"
  printf 'Exact query:\n%s\n\n' "${query}"
  printf 'Command:\n'
  printf '  cd code && uv run python -u flow.py -v %q\n' "${query}"
  printf '\n'
}

extract_session_id() {
  local log_file="$1"
  sed -n 's/.*session \(s8-[[:alnum:]_-]*\).*/\1/p' "${log_file}" | head -n 1
}

print_session_footer() {
  local label="$1"
  local sid="$2"
  if [[ -z "${sid}" ]]; then
    printf '\n[%s] WARNING: could not parse session id from flow.py output.\n' "${label}"
    return 1
  fi

  printf '\n[%s] session id: %s\n' "${label}" "${sid}"
  printf '[%s] session path: %s\n' "${label}" "${CODE_DIR}/state/sessions/${sid}"
  printf '[%s] replay hint: cd code && uv run python replay.py %s\n' "${label}" "${sid}"
}

check_gateway() {
  section "Gateway Preflight"
  printf 'Checking http://localhost:8108/v1/routers ...\n'
  if curl -s --max-time 5 "http://localhost:8108/v1/routers" >/dev/null; then
    printf 'Gateway is healthy.\n'
    return 0
  fi

  cat <<'MSG'
Gateway is not healthy on http://localhost:8108.

Start it in another terminal, then rerun this script:

  cd gateway && uv run main.py
MSG
  exit 1
}

run_flow() {
  local label="$1"
  local query="$2"
  local log_file="${TMP_DIR}/${label//[^A-Za-z0-9_.-]/_}.log"

  print_run_header "${label}" "${query}"
  (
    cd "${CODE_DIR}"
    uv run python -u flow.py -v "${query}"
  ) 2>&1 | tee "${log_file}"

  local sid
  sid="$(extract_session_id "${log_file}")"
  print_session_footer "${label}" "${sid}"
}

graph_has_running_node() {
  local sid="$1"
  local graph_path="${CODE_DIR}/state/sessions/${sid}/graph.json"

  [[ -f "${graph_path}" ]] || return 1
  python3 - "${graph_path}" <<'PY'
import json
import sys
from pathlib import Path

graph_path = Path(sys.argv[1])
try:
    payload = json.loads(graph_path.read_text())
except Exception:
    sys.exit(1)

for node in payload.get("nodes", []):
    if isinstance(node, dict) and node.get("status") == "running":
        sys.exit(0)
sys.exit(1)
PY
}

wait_for_session_id() {
  local log_file="$1"
  local deadline="$2"
  local sid=""

  while (( SECONDS < deadline )); do
    sid="$(extract_session_id "${log_file}")"
    if [[ -n "${sid}" ]]; then
      printf '%s\n' "${sid}"
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_running_node() {
  local sid="$1"
  local timeout_s="$2"
  local deadline=$((SECONDS + timeout_s))

  while (( SECONDS < deadline )); do
    if graph_has_running_node "${sid}"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

run_kill_resume() {
  local label="K Resume"
  local query="$1"
  local killed_log="${TMP_DIR}/killed_k.log"
  local resume_log="${TMP_DIR}/resumed_k.log"
  local sid=""
  local child_pid=""

  section "${label}"
  printf 'Exact query:\n%s\n\n' "${query}"
  printf 'Initial command:\n'
  printf '  cd code && uv run python -u flow.py -v %q\n\n' "${query}"

  (
    cd "${CODE_DIR}"
    exec uv run python -u flow.py -v "${query}"
  ) >"${killed_log}" 2>&1 &
  child_pid=$!
  tail -n +1 -f "${killed_log}" &
  local tail_pid=$!

  if ! sid="$(wait_for_session_id "${killed_log}" $((SECONDS + 45)))"; then
    printf '\n[%s] ERROR: could not parse session id before timeout.\n' "${label}"
    kill "${tail_pid}" 2>/dev/null || true
    kill "${child_pid}" 2>/dev/null || true
    wait "${child_pid}" || true
    wait "${tail_pid}" 2>/dev/null || true
    return 1
  fi

  printf '\n[%s] parsed session id before kill: %s\n' "${label}" "${sid}"
  printf '[%s] waiting for persisted running node in graph.json ...\n' "${label}"
  if wait_for_running_node "${sid}" 120; then
    printf '[%s] running node detected; killing child process %s.\n' "${label}" "${child_pid}"
  else
    printf '[%s] WARNING: no running node detected before timeout; killing child process %s for resume demo anyway.\n' "${label}" "${child_pid}"
  fi

  if kill "${child_pid}" 2>/dev/null; then
    printf '[%s] sent TERM to child process %s.\n' "${label}" "${child_pid}"
  else
    printf '[%s] child process %s was already exited.\n' "${label}" "${child_pid}"
  fi
  wait "${child_pid}" || true
  kill "${tail_pid}" 2>/dev/null || true
  wait "${tail_pid}" 2>/dev/null || true

  subsection "Resume"
  printf 'Resume command:\n'
  printf '  cd code && uv run python -u flow.py -v --resume %s\n\n' "${sid}"
  (
    cd "${CODE_DIR}"
    uv run python -u flow.py -v --resume "${sid}"
  ) 2>&1 | tee "${resume_log}"

  print_session_footer "${label}" "${sid}"
}

main() {
  cd "${REPO_ROOT}"
  check_gateway

  section "Submission Harness"
  printf 'Repository: %s\n' "${REPO_ROOT}"
  printf 'Agent code: %s\n' "${CODE_DIR}"
  printf 'Temporary parsing files: %s\n' "${TMP_DIR}"
  printf '\nTip: capture this console transcript with:\n'
  printf '  scripts/submission_run.sh | tee submission-transcript.txt\n'

  run_flow "hello" \
    "Say hello."

  run_flow "A - Shannon Wikipedia" \
    "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory."

  run_flow "I - City Populations" \
    "Find the populations of London, Paris, Berlin and tell me which two are closest in size."

  run_flow "J - Local Path Fail Fast" \
    "Read /nonexistent/path.txt and tell me what's in it."

  run_kill_resume \
    "For Lagos, Cairo, and Kinshasa, find current populations and growth rates and tell me which is growing fastest."

  run_flow "Auction Strategy" \
    "Compare Jasprit Bumrah, Rashid Khan, Andre Russell, and Suryakumar Yadav for an IPL-style auction. For each player, research primary role, recent form, injury or fitness risk, match impact, role scarcity, and estimated auction value. With a budget of 19 crore, recommend the best two-player purchase strategy."

  run_flow "Critic Pass Demo" \
    "Controlled critic pass demo: Extract an IPL auction player card from this inline source text and render the card. Source text: player_name=Jasprit Bumrah; primary_role=fast bowler and death-overs specialist; recent_form_score=9; match_impact_score=10; role_scarcity_score=9; fitness_score=8; price_efficiency_score=6; estimated_price_crore=11; auction_risk=low risk, workload management required."

  run_flow "Critic Fail Recovery Demo" \
    "Controlled critic fail demo: First extract an IPL auction player card only from this inline source text and render the card. Source text: player_name=Jasprit Bumrah; primary_role=fast bowler and death-overs specialist; recent_form_score=9; match_impact_score=10; role_scarcity_score=9; price_efficiency_score=6; estimated_price_crore=11; auction_risk=fitness status not provided. The source intentionally omits fitness_score; do not infer or invent fitness_score from auction_risk or any outside knowledge."

  section "Done"
  printf 'All planned evidence runs have been invoked.\n'
  printf 'Detailed per-session evidence is under: %s/state/sessions\n' "${CODE_DIR}"
}

main "$@"
