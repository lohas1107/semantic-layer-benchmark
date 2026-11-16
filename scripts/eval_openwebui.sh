#!/usr/bin/env bash
# Agentic Open WebUI eval with the Nebius GPU endpoint started only for the run.
#
#   scripts/eval_openwebui.sh [--run-id ID] [--semantic] [--limit N] [--only 1,2]
#                             [--concurrency N] [--no-score]
#
# Order: checks (no GPU) -> reference rows (no GPU) -> start endpoint -> wait for
# /v1/models -> sync Open WebUI connection + run_sql tool -> run_openwebui.py ->
# STOP endpoint (also on error/Ctrl-C via trap) -> judge + report (no GPU).
# Reuse --run-id for the --semantic pass so report_run pairs raw vs semantic.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
NEBIUS_DIR=${NEBIUS_DIR:-/home/agent/workspace/nebius}
MODEL=${MODEL:-google/gemma-4-26B-A4B-it}
READY_TIMEOUT_S=${READY_TIMEOUT_S:-2400}   # cold start downloads ~52 GB of weights

RUN_ID="" SCORE=1 RUNNER_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --run-id) RUN_ID=$2; shift 2 ;;
    --no-score) SCORE=0; shift ;;
    --semantic) RUNNER_ARGS+=("$1"); shift ;;
    --limit|--only|--concurrency|--timeout) RUNNER_ARGS+=("$1" "$2"); shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
RUN_ID=${RUN_ID:-pg-$(date -u +%Y%m%d-%H%M%S)}
cd "$REPO"
set -a; [ -f .env ] && . ./.env; set +a
: "${OPENWEBUI_API_KEY:?set OPENWEBUI_API_KEY in .env}" "${PG_DSN:?set PG_DSN in .env}"
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# --- checks that need no GPU ------------------------------------------------
log "run id: $RUN_ID"
uv run python -c "from benchmarks.text_to_sql.pg_client import make_pg_client as m; assert m().ping(), 'PG not reachable'"
curl -fsS -o /dev/null "${OPENWEBUI_URL:-http://localhost:3000}/health"
if [ ! -d "runs/$RUN_ID/reference" ] || [ "$(ls "runs/$RUN_ID/reference" | wc -l)" -lt 100 ]; then
  log "reference rows"
  uv run python scripts/run_reference.py --run-id "$RUN_ID" --engine postgres
fi

# --- Nebius endpoint ----------------------------------------------------------
pushd "$NEBIUS_DIR" >/dev/null
export NEBIUS_IAM_TOKEN=${NEBIUS_IAM_TOKEN:-$(nebius iam get-access-token)}
EP=$(pulumi stack output endpointId)
URL=$(pulumi stack output endpointUrl)
TOKEN=$(pulumi stack output endpointToken --show-secrets)
popd >/dev/null
state() { nebius ai endpoint get "$EP" --format jsonpath='{.status.state}' 2>/dev/null || echo unknown; }

STOPPED=0
stop_endpoint() {
  [ "$STOPPED" = 1 ] && return
  STOPPED=1
  log "stopping endpoint $EP"
  nebius ai endpoint stop "$EP" >/dev/null || log "WARNING: stop command failed"
  for _ in $(seq 60); do
    s=$(state); [ "$s" = STOPPED ] && { log "endpoint STOPPED"; return; }
    sleep 10
  done
  log "WARNING: endpoint state is '$(state)', check it manually: nebius ai endpoint get $EP"
}
trap stop_endpoint EXIT INT TERM

s=$(state)
if [ "$s" = RUNNING ]; then
  log "endpoint $EP already RUNNING, not starting it again"
else
  log "starting endpoint $EP (state: $s)"
  nebius ai endpoint start "$EP" >/dev/null
fi
deadline=$(( $(date +%s) + READY_TIMEOUT_S ))
until curl -fsS -m 10 -o /dev/null -H "Authorization: Bearer $TOKEN" "$URL/v1/models"; do
  [ "$(date +%s)" -gt "$deadline" ] && { log "endpoint not ready after ${READY_TIMEOUT_S}s"; exit 1; }
  sleep 20
done
log "endpoint ready"

NEBIUS_BASE_URL="$URL/v1" NEBIUS_TOKEN="$TOKEN" OPENWEBUI_MODEL="$MODEL" \
  uv run python scripts/openwebui_setup.py
RUNNER_OK=1
uv run python scripts/run_openwebui.py --run-id "$RUN_ID" --model "$MODEL" "${RUNNER_ARGS[@]}" \
  || RUNNER_OK=0

stop_endpoint
trap - EXIT INT TERM

if [ "$RUNNER_OK" = 0 ]; then
  # Unanswered questions have no output file; scoring now would record them as
  # missing_input failures that a later resume would not re-score.
  log "runner left questions unanswered (infra errors); not scoring."
  log "rerun: scripts/eval_openwebui.sh --run-id $RUN_ID ${RUNNER_ARGS[*]}"
  exit 1
fi

# --- judge and report (no GPU) -----------------------------------------------
if [ "$SCORE" = 1 ]; then
  uv run python scripts/score_run.py --run-id "$RUN_ID" --engine postgres
  uv run python scripts/report_run.py --run-id "$RUN_ID"
fi
log "done: runs/$RUN_ID"
