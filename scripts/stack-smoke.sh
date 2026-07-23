#!/usr/bin/env bash
# Smoke-test the running FreeTier Atlas stack against ground truth.
#
# Verifies the live API liveness endpoint (200), readiness endpoint (200 with
# database reachable), that the Alembic migrations created the scaffold app_meta
# table plus the worker job_queue and service_heartbeat tables, that the worker
# and scheduler containers are healthy, that at least one queued job reached
# 'done', and that both service heartbeats are fresh. Resolves the repository
# root from the script's own path. Requires the stack to be running (stack-up).
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd -- "${REPO_ROOT}"

API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-8080}"
PG_USER="${POSTGRES_USER:-atlas}"
PG_DB="${POSTGRES_DB:-atlas}"

failures=()

run_check() {
  local name="$1"
  shift
  echo "==> ${name}"
  if "$@"; then
    echo "    PASS: ${name}"
  else
    echo "    FAIL: ${name}"
    failures+=("${name}")
  fi
}

psql_query() {
  docker compose exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" -tAc "$1" | tr -d '[:space:]'
}

container_health() {
  local cid
  cid="$(docker compose ps -q "$1" | tr -d '[:space:]')"
  [[ -n "${cid}" ]] || { echo ""; return; }
  docker inspect -f '{{.State.Health.Status}}' "${cid}" | tr -d '[:space:]'
}

# Poll a shell condition until it succeeds or a timeout elapses.
wait_until() {
  local timeout="$1"
  shift
  local deadline=$(( $(date +%s) + timeout ))
  while [[ "$(date +%s)" -lt "${deadline}" ]]; do
    if "$@"; then return 0; fi
    sleep 3
  done
  return 1
}

check_liveness() {
  local body
  body="$(curl -fsS -m 5 "http://localhost:${API_PORT}/health")" || return 1
  echo "${body}" | grep -q '"status":"ok"'
}

check_readiness() {
  local body
  body="$(curl -fsS -m 5 "http://localhost:${API_PORT}/health/ready")" || return 1
  echo "${body}" | grep -q '"status":"ready"' && echo "${body}" | grep -q '"database":"ok"'
}

check_migration() {
  local regclass marker
  regclass="$(psql_query "SELECT to_regclass('public.app_meta')")"
  [[ "${regclass}" == "app_meta" ]] || return 1
  marker="$(psql_query "SELECT value FROM app_meta WHERE key='scaffold_initialized'")"
  [[ "${marker}" == "true" ]]
}

check_worker_migration() {
  local jq sh
  jq="$(psql_query "SELECT to_regclass('public.job_queue')")"
  [[ "${jq}" == "job_queue" ]] || return 1
  sh="$(psql_query "SELECT to_regclass('public.service_heartbeat')")"
  [[ "${sh}" == "service_heartbeat" ]]
}

check_domain_migration() {
  local r trg
  for t in provider service offer offer_version evidence; do
    r="$(psql_query "SELECT to_regclass('public.${t}')")"
    [[ "${r}" == "${t}" ]] || return 1
  done
  trg="$(psql_query "SELECT count(*) FROM pg_trigger WHERE tgname='trg_offer_version_immutable'")"
  [[ "${trg}" == "1" ]]
}

check_ingest_migration() {
  local r link
  for t in candidate discovery_candidate; do
    r="$(psql_query "SELECT to_regclass('public.${t}')")"
    [[ "${r}" == "${t}" ]] || return 1
  done
  link="$(psql_query "SELECT count(*) FROM pg_constraint WHERE conname='ck_evidence_evidence_link_target'")"
  [[ "${link}" == "1" ]]
}

check_separation_migration() {
  local trg imm
  trg="$(psql_query "SELECT count(*) FROM pg_trigger WHERE tgname IN ('trg_candidate_official_source','trg_evidence_official_candidate')")"
  [[ "${trg}" == "2" ]] || return 1
  # The 0006 downgrade must never touch the offer_version immutability trigger.
  imm="$(psql_query "SELECT count(*) FROM pg_trigger WHERE tgname='trg_offer_version_immutable'")"
  [[ "${imm}" == "1" ]]
}

check_source_slug_migration() {
  local col uc
  col="$(psql_query "SELECT count(*) FROM information_schema.columns WHERE table_name='source' AND column_name='slug'")"
  [[ "${col}" == "1" ]] || return 1
  uc="$(psql_query "SELECT count(*) FROM pg_constraint WHERE conname='uq_source_slug'")"
  [[ "${uc}" == "1" ]]
}

worker_healthy() { [[ "$(container_health worker)" == "healthy" ]]; }
scheduler_healthy() { [[ "$(container_health scheduler)" == "healthy" ]]; }

job_done() {
  local n
  n="$(psql_query "SELECT count(*) FROM job_queue WHERE status='done'")"
  [[ "${n}" =~ ^[0-9]+$ && "${n}" -ge 1 ]]
}

check_worker_healthy() { wait_until 90 worker_healthy; }
check_scheduler_healthy() { wait_until 90 scheduler_healthy; }
check_queue_processed() { wait_until 90 job_done; }

check_heartbeats_fresh() {
  local n
  n="$(psql_query "SELECT count(*) FROM service_heartbeat WHERE service IN ('worker','scheduler') AND last_beat_at > now() - interval '60 seconds'")"
  [[ "${n}" == "2" ]]
}

web_healthy() { [[ "$(container_health web)" == "healthy" ]]; }
check_web_healthy() { wait_until 90 web_healthy; }

check_web_serves_spa() {
  local body
  body="$(curl -fsS -m 5 "http://localhost:${WEB_PORT}/")" || return 1
  echo "${body}" | grep -q 'id="root"'
}

check_web_proxies_api() {
  local body
  body="$(curl -fsS -m 5 "http://localhost:${WEB_PORT}/api/health")" || return 1
  echo "${body}" | grep -q '"status":"ok"'
}

run_check "API liveness (/health = 200)" check_liveness
run_check "API readiness (/health/ready = 200, db ok)" check_readiness
run_check "Migration applied (app_meta table + marker row)" check_migration
run_check "Worker migration applied (job_queue + service_heartbeat)" check_worker_migration
run_check "Domain migration applied (0003 tables + immutability trigger)" check_domain_migration
run_check "Ingest migration applied (0004 candidate + discovery_candidate tables)" check_ingest_migration
run_check "Separation migration applied (0006 quarantine triggers)" check_separation_migration
run_check "Source-slug migration applied (0007 source.slug + uq_source_slug)" check_source_slug_migration
run_check "Worker container healthy" check_worker_healthy
run_check "Scheduler container healthy" check_scheduler_healthy
run_check "Queue processed (>=1 job reached done)" check_queue_processed
run_check "Heartbeats fresh (worker + scheduler)" check_heartbeats_fresh
run_check "Web container healthy" check_web_healthy
run_check "Web serves SPA (GET / = 200 HTML with #root)" check_web_serves_spa
run_check "Web proxies API (GET /api/health = 200, status ok)" check_web_proxies_api

echo ""
if [[ "${#failures[@]}" -gt 0 ]]; then
  echo "STACK SMOKE FAILED: ${failures[*]}"
  exit 1
fi
echo "STACK SMOKE PASSED"
