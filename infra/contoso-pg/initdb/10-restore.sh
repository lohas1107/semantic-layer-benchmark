#!/bin/bash
# First start only (docker-entrypoint-initdb.d): restore the dataset, re-create
# planner statistics and database settings, add read-only roles:
#   admin      full read-only access to all tables/columns
#   marketing  restricted column-level read-only access
# Env:
#   PG_ADMIN_PASSWORD      password of role admin (default: admin)
#   PG_MARKETING_PASSWORD  password of role marketing (default: marketing)
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
     -v schema="$schema" \
     -v admin_pw="${PG_ADMIN_PASSWORD:-admin}" \
     -v marketing_pw="${PG_MARKETING_PASSWORD:-marketing}" <<'SQL'
CREATE ROLE admin LOGIN PASSWORD :'admin_pw';
GRANT CONNECT ON DATABASE contoso TO admin;
GRANT USAGE ON SCHEMA :"schema" TO admin;
GRANT SELECT ON ALL TABLES IN SCHEMA :"schema" TO admin;
ALTER ROLE admin SET default_transaction_read_only = on;
ALTER ROLE admin SET statement_timeout = '60s';
ALTER ROLE admin SET search_path = :"schema", public;

CREATE ROLE marketing LOGIN PASSWORD :'marketing_pw';
GRANT CONNECT ON DATABASE contoso TO marketing;
GRANT USAGE ON SCHEMA :"schema" TO marketing;
ALTER ROLE marketing SET default_transaction_read_only = on;
ALTER ROLE marketing SET statement_timeout = '60s';
ALTER ROLE marketing SET search_path = :"schema", public;

GRANT SELECT ON
  :"schema".dimaccount,
  :"schema".dimchannel,
  :"schema".dimcurrency,
  :"schema".dimdate,
  :"schema".dimentity,
  :"schema".dimgeography,
  :"schema".dimmachine,
  :"schema".dimoutage,
  :"schema".dimproduct,
  :"schema".dimproductcategory,
  :"schema".dimproductsubcategory,
  :"schema".dimpromotion,
  :"schema".dimscenario,
  :"schema".factexchangerate,
  :"schema".factitmachine,
  :"schema".factitsla,
  :"schema".factinventory,
  :"schema".factonlinesales,
  :"schema".factsales,
  :"schema".factsalesquota,
  :"schema".factstrategyplan
TO marketing;

GRANT SELECT (rownum, customerkey, geographykey)
  ON :"schema".dimcustomer TO marketing;

GRANT SELECT (
  employeekey, parentemployeekey, title, hiredate,
  departmentname, startdate, status, issalesperson
) ON :"schema".dimemployee TO marketing;

GRANT SELECT (
  storekey, geographykey, storetype, storename, status,
  opendate, closedate, entitykey, storephone, storefax,
  closereason, employeecount, sellingareasize, lastremodeldate, employeekey
) ON :"schema".dimstore TO marketing;

GRANT SELECT (
  salesterritorykey, geographykey, salesterritoryname, salesterritoryregion,
  salesterritorycountry, salesterritorygroup, salesterritorylevel, status, employeekey
) ON :"schema".dimsalesterritory TO marketing;
SQL
echo "contoso: ready"
