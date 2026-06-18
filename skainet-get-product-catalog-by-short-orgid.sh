#!/bin/bash

# Check if organization ID argument is provided
if [ -z "$1" ]; then
  echo "Error: Short organization ID is required"
  echo "Usage: $0 <short-orgid>"
  exit 1
fi

SHORT_ORGID="$1"

# Make the API call and capture the response
RESPONSE=$(curl -s "https://productcatalog.prod.microservice.skaivision.net/catalog/config/orgs/${SHORT_ORGID}" \
  -H 'accept: */*' \
  -H 'accept-language: en-US,en;q=0.9' \
  -H 'content-type: application/json' \
  -H 'origin: https://internaltools.skaivision.net' \
  -H 'priority: u=1, i' \
  -H 'referer: https://internaltools.skaivision.net/' \
  -H 'sec-ch-ua: "Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "macOS"' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: same-site' \
  -H 'user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')

# Check if jq is available
if ! command -v jq &> /dev/null; then
  echo "Error: jq is required to parse JSON. Please install jq."
  exit 1
fi

# Extract and output the phone numbers
SALES_PHONE=$(echo "$RESPONSE" | jq -r '.. | select(type == "object" and has("salesPhoneNumber")) | .salesPhoneNumber' | head -n 1)
OFFER_PHONE=$(echo "$RESPONSE" | jq -r '.. | select(type == "object" and has("offerPhoneNumber")) | .offerPhoneNumber' | head -n 1)
DMS_SUBSCRIPTION_ID=$(echo "$RESPONSE" | jq -r '.. | objects | select(has("subscriptionId")) | .subscriptionId' | head -n 1)

echo "salesPhoneNumber: ${SALES_PHONE:-N/A}"
echo "offerPhoneNumber: ${OFFER_PHONE:-N/A}"
echo "subscriptionId: ${DMS_SUBSCRIPTION_ID:-N/A}"
# echo "$RESPONSE" | jq .
