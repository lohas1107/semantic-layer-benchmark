#!/usr/bin/env bash
# Build the Contoso PostgreSQL images.
#
#   ./build.sh dumps KAGGLE_ZIP   load both variants into a scratch PG17, verify, pg_dump
#   ./build.sh images             docker build both tags from dist/*.dump
#   ./build.sh push               docker push both tags (ask before running: public)
#
# Sources
#   original: Hugging Face jocapc/ContosoRetailsDW @ pinned revision (Microsoft's
#             Synapse export of ContosoRetailDW) + Microsoft's official load DDL
#             in source/. Downloaded here and checked against work/jocapc.sha256.
#   cube:     Kaggle bhanuthakurr/cleaned-contoso-dataset (the CSVs the Cube paper
#             loaded into ClickHouse). Kaggle needs a login, so pass the zip you
#             downloaded; it is streamed, never extracted.
set -euo pipefail
cd "$(dirname "$0")"
REPO=$(cd ../.. && pwd)

IMAGE=${IMAGE:-lohas1107/contoso}
VERSION=${VERSION:-1.0.0}
TAG_ORIGINAL="pg-${VERSION}"
TAG_CUBE="pg-cube-${VERSION}"
HF_REPO=jocapc/ContosoRetailsDW
HF_REVISION=f3ec01ed96a2896be66bcef2a971bf257fedaf80
PG_CONTAINER=contoso-build
PG_PORT=55433
DSN="postgresql://postgres:build@127.0.0.1:${PG_PORT}/contoso"
TABLES="DimAccount DimChannel DimCurrency DimCustomer DimDate DimEmployee DimEntity
DimGeography DimMachine DimOutage DimProduct DimProductCategory DimProductSubcategory
DimPromotion DimSalesTerritory DimScenario DimStore FactExchangeRate FactITMachine
FactITSLA FactInventory FactOnlineSales FactSales FactSalesQuota FactStrategyPlan"

fetch_jocapc() {
  mkdir -p work/jocapc-zip
  for t in $TABLES; do
    [ -s "work/jocapc-zip/$t.zip" ] && continue
    curl -fsSL --retry 3 -o "work/jocapc-zip/$t.zip" \
      "https://huggingface.co/datasets/${HF_REPO}/resolve/${HF_REVISION}/$t.zip"
  done
  (cd work/jocapc-zip && sha256sum -c --quiet ../../jocapc.sha256)
}

start_pg() {
  docker rm -fv "$PG_CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$PG_CONTAINER" -p "127.0.0.1:${PG_PORT}:5432" --shm-size=1g \
    -e POSTGRES_PASSWORD=build -e POSTGRES_DB=contoso \
    -e POSTGRES_INITDB_ARGS="--locale=C --encoding=UTF8" \
    postgres:17 -c maintenance_work_mem=1GB -c max_wal_size=8GB >/dev/null
  until docker exec "$PG_CONTAINER" pg_isready -U postgres -d contoso -q; do sleep 1; done
  sleep 2
}

psql_in() { docker exec -i "$PG_CONTAINER" psql -U postgres -d contoso -q -v ON_ERROR_STOP=1 "$@"; }

dump() {  # $1 variant, $2 schema
  docker exec "$PG_CONTAINER" pg_dump -U postgres -d contoso -n "$2" -Fc -Z zstd:9 \
    -f "/tmp/contoso-$1.dump"
  docker cp "$PG_CONTAINER:/tmp/contoso-$1.dump" "dist/contoso-$1.dump"
}

cmd_dumps() {
  local kaggle_zip=${1:?usage: ./build.sh dumps KAGGLE_ZIP}
  mkdir -p dist
  fetch_jocapc
  start_pg
  trap 'docker rm -fv "$PG_CONTAINER" >/dev/null' EXIT
  (cd "$REPO" && uv run python docker/contoso/load_jocapc.py --dsn "$DSN" --zips docker/contoso/work/jocapc-zip)
  (cd "$REPO" && uv run python docker/contoso/load_kaggle.py --dsn "$DSN" --zip "$kaggle_zip")
  psql_in -v schema=dbo < tune.sql
  psql_in -v schema=retail < tune.sql
  (cd "$REPO" && PG_DSN="$DSN" uv run python docker/contoso/verify_pg_dataset.py --timeout 300)
  dump original dbo
  dump cube retail
  (cd dist && sha256sum contoso-*.dump > SHA256SUMS && ls -l)
}

cmd_images() {
  docker build --build-arg VARIANT=original --build-arg VERSION="$VERSION" -t "$IMAGE:$TAG_ORIGINAL" .
  docker build --build-arg VARIANT=cube --build-arg VERSION="$VERSION" -t "$IMAGE:$TAG_CUBE" .
}

cmd_push() {
  docker push "$IMAGE:$TAG_ORIGINAL"
  docker push "$IMAGE:$TAG_CUBE"
}

case "${1:-}" in
  dumps) shift; cmd_dumps "$@" ;;
  images) cmd_images ;;
  push) cmd_push ;;
  *) sed -n '2,15p' "$0"; exit 2 ;;
esac
