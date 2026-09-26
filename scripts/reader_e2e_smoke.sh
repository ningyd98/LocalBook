#!/usr/bin/env bash
# End-to-end Reader API smoke test against a running local server.
set -uo pipefail

BASE="${BASE:-http://127.0.0.1:3780}"
PASS=0
FAIL=0

check() { # check <label> <actual> <expected-substring>
  if [[ "$2" == *"$3"* ]]; then
    echo "  PASS  $1"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  $1"
    echo "        expected substring: $3"
    echo "        actual: ${2:0:300}"
    FAIL=$((FAIL + 1))
  fi
}

echo "== 1. capabilities =="
R=$(curl -s "$BASE/api/v1/reader/capabilities")
check "returns version 1.0" "$R" '"version":"1.0"'
check "advertises ai_chat" "$R" '"ai_chat":true'

echo "== 2. pair (device_type=ios) =="
R=$(curl -s -X POST "$BASE/api/v1/reader/pair" -H 'Content-Type: application/json' \
     -d '{"device_name":"E2E iPad","device_type":"ios"}')
TOKEN=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("pairing_token",""))' 2>/dev/null)
if [[ -n "$TOKEN" ]]; then
  echo "  PASS  pairing response contains a token"
  PASS=$((PASS + 1))
else
  echo "  FAIL  pairing response did not contain a token"
  FAIL=$((FAIL + 1))
fi
if [[ ${#TOKEN} -eq 6 ]]; then
  echo "  PASS  token is 6 digits ($TOKEN)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  token not 6 digits: '$TOKEN'"
  FAIL=$((FAIL + 1))
fi

echo "== 3. pair rejects bad device_type =="
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/reader/pair" \
       -H 'Content-Type: application/json' -d '{"device_name":"x","device_type":"tablet"}')
check "422 on invalid device_type" "$CODE" "422"

echo "== 4. register device =="
DEV_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
R=$(curl -s -X POST "$BASE/api/v1/reader/register" -H 'Content-Type: application/json' \
     -d "{\"device_id\":\"$DEV_ID\",\"device_name\":\"E2E iPad\",\"device_type\":\"ios\",\"pairing_token\":\"$TOKEN\"}")
check "returns access_token" "$R" '"access_token"'
ACCESS=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null)

echo "== 5. register rejects reused token =="
R=$(curl -s -X POST "$BASE/api/v1/reader/register" -H 'Content-Type: application/json' \
     -d "{\"device_id\":\"01943e51-0000-7000-8000-000000000002\",\"device_name\":\"Replay\",\"device_type\":\"ios\",\"pairing_token\":\"$TOKEN\"}")
check "pairing token is single-use" "$R" 'error'

echo "== 6. list devices =="
R=$(curl -s "$BASE/api/v1/reader/devices" -H "Authorization: Bearer $ACCESS")
check "device appears in list" "$R" "$DEV_ID"

echo "== 7. device status (authenticated) =="
R=$(curl -s "$BASE/api/v1/reader/status" -H "Authorization: Bearer $ACCESS")
check "status returns device_id" "$R" "$DEV_ID"
check "status reports active" "$R" '"is_active":true'

echo "== 8. status without auth is rejected =="
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/reader/status")
check "401 without Authorization" "$CODE" "401"

echo "== 9. sync push =="
R=$(curl -s -X POST "$BASE/api/v1/reader/sync/push" -H 'Content-Type: application/json' \
     -H "Authorization: Bearer $ACCESS" -d '{"operations":[{
       "operation":"create","entity_type":"source",
       "entity_id":"01943e60-0000-7000-8000-000000000010",
       "data":{"title":"E2E Source","source_type":"web","url":"https://example.com/a"},
       "client_timestamp":1700000000}]}')
check "push accepted" "$R" '"accepted"'

echo "== 10. sync pull =="
R=$(curl -s "$BASE/api/v1/reader/sync/pull" -H "Authorization: Bearer $ACCESS")
check "pull has next_cursor field" "$R" '"has_more"'

echo "== 11. search =="
R=$(curl -s -X POST "$BASE/api/v1/reader/search" -H 'Content-Type: application/json' \
     -H "Authorization: Bearer $ACCESS" -d '{"device_id":"'"$DEV_ID"'","query":"E2E","limit":5}')
check "search returns results key" "$R" '"results"'

echo
echo "===== RESULT: $PASS passed, $FAIL failed ====="
[[ $FAIL -eq 0 ]]
