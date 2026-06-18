curl -s 'https://healthcheck.prod.microservice.skaivision.net/skaibox/summary' \
  -H 'Content-Type: application/json' \
  --data '{
    "query": "",
    "skaiboxIds": ["EfFCi-PPgoK4HAr_-b3vXQ"],
    "organizationIds": [],
    "filterBy": {
      "monitoredStatus": "ANY",
      "serverStatus": "ANY",
      "vpnStatus": "ANY",
      "serverHealthStatus": ["ANY"],
      "billingStatus": "ANY",
      "serverType": ["ANY"],
      "statusCodes": ["ANY"],
      "vendors": ["ANY"]
    },
    "startIndex": 0,
    "pageSize": 1
  }' | jq '{
    skaiboxId: .items[0].details.id,
    orgShortId: .items[0].organization.id,
    orgLongId: .items[0].organization.longId,
    orgName: .items[0].organization.name
  }'