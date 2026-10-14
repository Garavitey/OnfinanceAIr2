#!/usr/bin/env bash
# Local health probe.
#
# Checks what is actually configured for THIS machine (no Docker): the
# project venv, the configured database (SQLite by default, Postgres only if
# DATABASE_URL says so), the optional Redis cache, the local PlantUML jar,
# and the Mermaid parse-validator sidecar.
#
# Prints one pass/fail/skip line per check. Exits non-zero only if a
# non-skipped check FAILS; skips never fail the script.
set -u

cd "$(dirname "$0")/.." || exit 1

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

EXIT_CODE=0

pass() { printf 'PASS  %-10s %s\n' "$1" "$2"; }
fail() { printf 'FAIL  %-10s %s\n' "$1" "$2"; EXIT_CODE=1; }
skip() { printf 'SKIP  %-10s %s\n' "$1" "$2"; }

# --- 1. python venv -----------------------------------------------------
check_python() {
  name="python"
  if [ ! -x ".venv/bin/python" ]; then
    fail "$name" ".venv/bin/python not found"
    return
  fi
  ver="$(.venv/bin/python --version 2>&1)"
  case "$ver" in
    *" 3.13."*) pass "$name" "$ver" ;;
    *) fail "$name" "expected 3.13.x, got: $ver" ;;
  esac
}

# --- 2. database ---------------------------------------------------------
check_database() {
  name="database"
  url="${DATABASE_URL:-sqlite+aiosqlite:///./uml.db}"
  case "$url" in
    sqlite*)
      # sqlite:///relative.db (3 slashes -> relative path) or
      # sqlite:////abs.db (4 slashes -> absolute path). Strip scheme + first
      # three slashes either way; what remains is the path.
      path="${url#*:///}"
      dir="$(dirname "$path")"
      if [ -d "$dir" ] && [ -w "$dir" ]; then
        pass "$name" "sqlite: $dir is writable"
      else
        fail "$name" "sqlite: $dir is not a writable directory"
      fi
      ;;
    postgres*)
      if ! command -v pg_isready >/dev/null 2>&1; then
        fail "$name" "postgres configured but pg_isready not found"
        return
      fi
      if pg_isready -d "$url" >/tmp/health_pg_isready.$$ 2>&1; then
        pass "$name" "postgres: $(cat /tmp/health_pg_isready.$$)"
      else
        fail "$name" "postgres unreachable: $(cat /tmp/health_pg_isready.$$)"
      fi
      rm -f /tmp/health_pg_isready.$$
      ;;
    *)
      fail "$name" "unrecognized DATABASE_URL scheme: $url"
      ;;
  esac
}

# --- 3. redis --------------------------------------------------------------
check_redis() {
  name="redis"
  if [ -z "${REDIS_URL:-}" ]; then
    skip "$name" "REDIS_URL unset, using in-process cache fallback"
    return
  fi
  if ! command -v redis-cli >/dev/null 2>&1; then
    fail "$name" "REDIS_URL set but redis-cli not found"
    return
  fi
  reply="$(redis-cli -u "$REDIS_URL" ping 2>&1)"
  if [ "$reply" = "PONG" ]; then
    pass "$name" "PONG from $REDIS_URL"
  else
    fail "$name" "no PONG from $REDIS_URL: $reply"
  fi
}

# --- 4. plantuml -----------------------------------------------------------
check_plantuml() {
  name="plantuml"
  jar="${PLANTUML_JAR:-vendor/plantuml.jar}"
  if ! command -v java >/dev/null 2>&1; then
    fail "$name" "java not found"
    return
  fi
  if [ ! -f "$jar" ]; then
    fail "$name" "$jar not found"
    return
  fi
  out="$(printf '@startuml\nAlice -> Bob: hi\n@enduml\n' | java -jar "$jar" -tsvg -pipe -failfast2 2>/tmp/health_plantuml_err.$$)"
  code=$?
  if [ "$code" -eq 0 ] && [ -n "$out" ]; then
    pass "$name" "$jar rendered SVG, exit 0"
  else
    fail "$name" "$jar exit $code: $(cat /tmp/health_plantuml_err.$$)"
  fi
  rm -f /tmp/health_plantuml_err.$$
}

# --- 5. mermaid validator ---------------------------------------------------
check_mermaid() {
  name="mermaid"
  if ! command -v node >/dev/null 2>&1; then
    fail "$name" "node not found"
    return
  fi
  if [ ! -d "validator/node_modules" ]; then
    fail "$name" "validator/node_modules not installed"
    return
  fi
  out="$(printf 'graph TD\nA-->B\n' | node validator/validate.mjs 2>/tmp/health_mermaid_err.$$)"
  case "$out" in
    *'"ok":true'*) pass "$name" "validator/validate.mjs: $out" ;;
    *) fail "$name" "validator/validate.mjs did not return ok:true: $out $(cat /tmp/health_mermaid_err.$$)" ;;
  esac
  rm -f /tmp/health_mermaid_err.$$
}

check_python
check_database
check_redis
check_plantuml
check_mermaid

exit "$EXIT_CODE"
