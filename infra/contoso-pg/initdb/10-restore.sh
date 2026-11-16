#!/bin/bash
# First start only (docker-entrypoint-initdb.d): restore the dataset, re-create
# planner statistics and database settings, add a read-only role.
#   BENCH_RO_PASSWORD  password of role bench_ro (default: bench_ro)
set -euo pipefail

case "$CONTOSO_VARIANT" in
  original) schema=$CONTOSO_SCHEMA_ORIGINAL ;;
  cube)     schema=$CONTOSO_SCHEMA_CUBE ;;
  *) echo "unknown CONTOSO_VARIANT=$CONTOSO_VARIANT" >&2; exit 1 ;;
esac

echo "contoso: restoring $CONTOSO_VARIANT (schema $schema)"
pg_restore --no-owner --exit-on-error -j "$(nproc)" -U "$POSTGRES_USER" -d "$POSTGRES_DB" /contoso/contoso.dump

# Expression statistics are in the dump; ANALYZE fills them. Settings are per-database.
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v schema="$schema" -f /contoso/tune.sql

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     -v schema="$schema" -v ro_pw="${BENCH_RO_PASSWORD:-bench_ro}" <<'SQL'
CREATE ROLE bench_ro LOGIN PASSWORD :'ro_pw';
GRANT CONNECT ON DATABASE contoso TO bench_ro;
GRANT USAGE ON SCHEMA :"schema" TO bench_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA :"schema" TO bench_ro;
ALTER ROLE bench_ro SET default_transaction_read_only = on;
ALTER ROLE bench_ro SET statement_timeout = '60s';
ALTER ROLE bench_ro SET search_path = :"schema", public;
SQL
echo "contoso: ready"
