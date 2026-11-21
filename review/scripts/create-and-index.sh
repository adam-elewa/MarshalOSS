#!/usr/bin/env bash
# Create a Datashare project and SCAN+INDEX a folder under ./data (or DATASHARE_DATA_DIR).
# Usage: ./scripts/create-and-index.sh <project-name> <subdir-under-data>
# Example: ./scripts/create-and-index.sh washington washington
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="${1:?project name}"
SUBDIR="${2:?subdir under data/}"
USER="${POSTGRES_USER:-datashare}"
PASS="${POSTGRES_PASSWORD:-datashare}"
DSURL="jdbc:postgresql://postgresql/datashare?user=${USER}&password=${PASS}"

docker compose run --rm datashare \
  project create "$PROJECT" \
  --label "$PROJECT" \
  --source-path "/home/datashare/data/${SUBDIR}" \
  --if-not-exists \
  --dataSourceUrl "$DSURL" \
  --elasticsearchAddress http://elasticsearch:9200

docker compose run --rm datashare \
  stage run \
  --stages SCAN,INDEX \
  --defaultProject "$PROJECT" \
  --dataDir "/home/datashare/data/${SUBDIR}" \
  --elasticsearchAddress http://elasticsearch:9200 \
  --reportName "report:${PROJECT}" \
  --queueType REDIS \
  --redisAddress redis://redis:6379 \
  --ocr false \
  --dataSourceUrl "$DSURL"

echo "Indexed into project '${PROJECT}'. Open http://localhost:${DATASHARE_PORT:-8100}/"
