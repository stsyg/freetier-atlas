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
# If Compose cannot be consulted at all (or no Python interpreter is available
# to read its JSON), the helpers fall back to the process environment and then
# to the documented default, which is exactly the old behaviour.
#
# Source this file; it defines functions and does not execute checks.
# shellcheck shell=bash

_STACK_COMPOSE_CONFIG=""
_STACK_COMPOSE_CONFIG_LOADED=0

_stack_python() {
  if command -v python3 >/dev/null 2>&1; then
    echo python3
  elif command -v python >/dev/null 2>&1; then
    echo python
  fi
}

_stack_compose_config() {
  if [[ "${_STACK_COMPOSE_CONFIG_LOADED}" -eq 0 ]]; then
    _STACK_COMPOSE_CONFIG_LOADED=1
    _STACK_COMPOSE_CONFIG="$(docker compose config --format json 2>/dev/null || true)"
  fi
  printf '%s' "${_STACK_COMPOSE_CONFIG}"
}

# _stack_compose_query <port|env> <service> <container-port|variable-name>
# Prints the resolved value on stdout, or exits non-zero when unavailable.
_stack_compose_query() {
  local mode="$1" service="$2" key="$3"
  local python config

  python="$(_stack_python)"
  [[ -n "${python}" ]] || return 1
  config="$(_stack_compose_config)"
  [[ -n "${config}" ]] || return 1

  printf '%s' "${config}" | "${python}" -c '
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
' "${mode}" "${service}" "${key}" 2>/dev/null
}

# stack_port <service> <container-port> <fallback-variable> <fallback-default>
stack_port() {
  local service="$1" container_port="$2" env_var="$3" default="$4" value
  value="$(_stack_compose_query port "${service}" "${container_port}")" || value=""
  if [[ -n "${value}" ]]; then
    printf '%s' "${value}"
    return 0
  fi
  printf '%s' "${!env_var:-${default}}"
}

# stack_service_setting <service> <variable-name> <fallback-default>
stack_service_setting() {
  local service="$1" name="$2" default="$3" value
  value="$(_stack_compose_query env "${service}" "${name}")" || value=""
  if [[ -n "${value}" ]]; then
    printf '%s' "${value}"
    return 0
  fi
  printf '%s' "${!name:-${default}}"
}
