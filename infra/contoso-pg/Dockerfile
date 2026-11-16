# Contoso retail data warehouse on PostgreSQL 17.
#   VARIANT=original -> schema dbo    : Microsoft ContosoRetailDW, unchanged values
#                                       (lohas1107/contoso:pg-1.0.0)
#   VARIANT=cube     -> schema retail : Cube benchmark copy (Kaggle "Cleaned Contoso")
#                                       (lohas1107/contoso:pg-cube-1.0.0)
# Build with ./build.sh, which produces dist/contoso-<variant>.dump first.
FROM postgres:17

ARG VARIANT
ARG VERSION=1.0.0
RUN test "$VARIANT" = original -o "$VARIANT" = cube

ENV POSTGRES_DB=contoso \
    POSTGRES_INITDB_ARGS="--locale=C --encoding=UTF8" \
    CONTOSO_VARIANT=${VARIANT} \
    CONTOSO_SCHEMA_ORIGINAL=dbo \
    CONTOSO_SCHEMA_CUBE=retail

LABEL org.opencontainers.image.title="contoso-${VARIANT}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.description="Contoso retail DW (${VARIANT}) for the semantic-layer text-to-SQL benchmark" \
      org.opencontainers.image.source="https://github.com/lohas1107/semantic-layer-benchmark"

COPY dist/contoso-${VARIANT}.dump /contoso/contoso.dump
COPY tune.sql /contoso/tune.sql
COPY initdb/10-restore.sh /docker-entrypoint-initdb.d/10-restore.sh
