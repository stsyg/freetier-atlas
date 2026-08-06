#!/usr/bin/env bash
# Resolve the development stack's host-facing settings from Docker Compose.
#
# `docker compose` automatically reads the project's .env file; the helper
# scripts historically read only the process environment, so ports set solely in
# .env made the scripts probe the wrong host ports against a perfectly healthy
# stack (false failures).
#
# Rather than reimplement .env parsing and Compose's precedence rules, these
# helpers ask Compose for its own resolved model (`docker compose config
# --format json`). Compose stays the single source of truth: the .env file, the
# process environment (which wins over .env), and the `${VAR:-default}` defaults
# declared in docker-compose.yml are all applied by Compose itself.
#
# If Compose cannot be consulted at all (Docker is not installed), the helpers
# fall back to the process environment and then to the documented default, which
# is exactly the old behaviour and is not worth reporting.
#
# Any OTHER failure is anomalous and is reported on stderr rather than absorbed:
# a silent fallback would reproduce the very false-failure bug this file exists
# to remove, while looking like a successful resolution.
#
# Source this file; it defines functions and does not execute checks.
# shellcheck shell=bash

_STACK_COMPOSE_CONFIG=""
_STACK_COMPOSE_CONFIG_LOADED=0
_STACK_PYTHON=""
_STACK_PYTHON_LOADED=0

_stack_warn() {
  printf 'stack-env: WARNING: %s\n' "$*" >&2
}

# Presence on PATH is not proof of function. The Microsoft Store ships a
# `python3` execution-alias stub that resolves via `command -v`, prints an
# advertisement instead of running, and is the first candidate on PATH under Git
# Bash. Accept a candidate only when it actually evaluates a trivial program and
# prints the expected sentinel; check the OUTPUT, because such stubs cannot be
# relied on to signal failure through their exit status.
_stack_python() {
  if [[ "${_STACK_PYTHON_LOADED}" -eq 1 ]]; then
    printf '%s' "${_STACK_PYTHON}"
    return 0
  fi
  _STACK_PYTHON_LOADED=1

  local candidate probe
  for candidate in python3 python py; do
    command -v "${candidate}" >/dev/null 2>&1 || continue
    probe="$("${candidate}" -c 'print("stack-env-ok")' 2>/dev/null </dev/null || true)"
    probe="${probe//$'\r'/}"
    if [[ "${probe}" == "stack-env-ok" ]]; then
      _STACK_PYTHON="${candidate}"
      break
    fi
  done

  if [[ -z "${_STACK_PYTHON}" ]]; then
    _stack_warn "no working Python interpreter found (tried python3, python, py); cannot read Compose's resolved configuration."
  fi

  printf '%s' "${_STACK_PYTHON}"
}

_stack_compose_config() {
  if [[ "${_STACK_COMPOSE_CONFIG_LOADED}" -eq 0 ]]; then
    _STACK_COMPOSE_CONFIG_LOADED=1
    if command -v docker >/dev/null 2>&1; then
      _STACK_COMPOSE_CONFIG="$(docker compose config --format json 2>/dev/null || true)"
      if [[ -z "${_STACK_COMPOSE_CONFIG}" ]]; then
        _stack_warn "'docker compose config' returned no configuration; falling back to environment variables and defaults."
      fi
    fi
  fi
  printf '%s' "${_STACK_COMPOSE_CONFIG}"
}

# _stack_compose_query <port|env> <service> <container-port|variable-name>
# Prints the resolved value on stdout. Returns 0 on success, 1 when Compose was
# not consulted at all (a quiet fallback is correct), or 2 when Compose returned
# a configuration but the value could not be extracted (anomalous; the caller
# reports it).
_stack_compose_query() {
  local mode="$1" service="$2" key="$3"
  local python config value

  config="$(_stack_compose_config)"
  [[ -n "${config}" ]] || return 1

  python="$(_stack_python)"
  [[ -n "${python}" ]] || return 2

  value="$(printf '%s' "${config}" | "${python}" -c '
import json
import sys

mode, service, key = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    model = json.load(sys.stdin)
except Exception:
    sys.exit(1)

service_model = (model.get("services") or {}).get(service) or {}

if mode == "port":
    ports = service_model.get("ports") or []
    if isinstance(ports, dict):
        ports = [ports]
    for mapping in ports:
        if not isinstance(mapping, dict):
            continue
        try:
            target = int(mapping.get("target", -1))
        except (TypeError, ValueError):
            continue
        published = mapping.get("published")
        if target == int(key) and published:
            print(published)
            sys.exit(0)
    sys.exit(1)

value = (service_model.get("environment") or {}).get(key)
if value is None or value == "":
    sys.exit(1)
print(value)
' "${mode}" "${service}" "${key}" 2>/dev/null)" || return 2
  value="${value//$'\r'/}"
  [[ -n "${value}" ]] || return 2
  printf '%s' "${value}"
}

# stack_port <service> <container-port> <fallback-variable> <fallback-default>
stack_port() {
  local service="$1" container_port="$2" env_var="$3" default="$4" value status
  value="$(_stack_compose_query port "${service}" "${container_port}")"
  status=$?
  if [[ ${status} -eq 0 && -n "${value}" ]]; then
    printf '%s' "${value}"
    return 0
  fi
  if [[ ${status} -eq 2 ]]; then
    _stack_warn "Compose returned a configuration but the published host port for service '${service}' (container port ${container_port}) could not be read; falling back to \$${env_var} or ${default}. The value below may not match the running stack."
  fi
  printf '%s' "${!env_var:-${default}}"
}

# stack_service_setting <service> <variable-name> <fallback-default>
stack_service_setting() {
  local service="$1" name="$2" default="$3" value status
  value="$(_stack_compose_query env "${service}" "${name}")"
  status=$?
  if [[ ${status} -eq 0 && -n "${value}" ]]; then
    printf '%s' "${value}"
    return 0
  fi
  if [[ ${status} -eq 2 ]]; then
    _stack_warn "Compose returned a configuration but '${name}' for service '${service}' could not be read; falling back to \$${name} or ${default}. The value below may not match the running stack."
  fi
  printf '%s' "${!name:-${default}}"
}
