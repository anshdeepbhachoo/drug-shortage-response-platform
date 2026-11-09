#!/bin/bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v owner="$POSTGRES_USER" -v dbname="$POSTGRES_DB" \
  -v ingest_pw="$INGESTION_DB_PASSWORD" -v ro_pw="$READONLY_DB_PASSWORD" <<'EOSQL'
CREATE ROLE ingestion_app LOGIN PASSWORD :'ingest_pw';
CREATE ROLE readonly_app  LOGIN PASSWORD :'ro_pw';
GRANT CONNECT ON DATABASE :"dbname" TO ingestion_app, readonly_app;
GRANT USAGE ON SCHEMA public TO ingestion_app, readonly_app;
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ingestion_app;
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT SELECT ON TABLES TO readonly_app;
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO ingestion_app;
EOSQL
