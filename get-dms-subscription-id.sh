#!/usr/bin/env bash
set -euo pipefail

SKAIBOX_ID="${1:?usage: $0 <skaibox_id>}"
HC_URL="https://healthcheck.prod.microservice.skaivision.net/skaibox/summary"
PC_BASE="https://productcatalog.prod.microservice.skaivision.net/catalog/config/orgs"

ORG_LONG_ID="$(
  curl -s "$HC_URL" \
    -H 'Content-Type: application/json' \
    --data "{
      \"query\": \"\",
      \"skaiboxIds\": [\"$SKAIBOX_ID\"],
      \"organizationIds\": [],
      \"filterBy\": {
        \"monitoredStatus\": \"ANY\",
        \"serverStatus\": \"ANY\",
        \"vpnStatus\": \"ANY\",
        \"serverHealthStatus\": [\"ANY\"],
        \"billingStatus\": \"ANY\",
        \"serverType\": [\"ANY\"],
        \"statusCodes\": [\"ANY\"],
        \"vendors\": [\"ANY\"]
      },
      \"startIndex\": 0,
      \"pageSize\": 1
    }" | jq -r '.items[0].organization.longId // empty'
)"

if [[ -z "$ORG_LONG_ID" ]]; then
  echo "No org found for SKAIBOX ID: $SKAIBOX_ID" >&2
  exit 1
fi

curl -s "$PC_BASE/$ORG_LONG_ID" \
  | jq -r '.productConfig.products[] | select(.productType == "SKAI_BOX") | .dms.subscriptionId // empty'