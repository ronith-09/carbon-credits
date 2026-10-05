#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
CARBONATE_ROOT=$(cd "$(dirname "$0")/.." && pwd)
NETWORK="$CARBONATE_ROOT/network"
export PATH="$CARBONATE_ROOT/.tools/bin:$CARBONATE_ROOT/.tools/go/bin:$PATH"
export FABRIC_CFG_PATH="$NETWORK"
export CORE_PEER_TLS_ENABLED=true
source "$NETWORK/network.config"
export DOCKER_SOCK="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)}"
DOCKER_SOCK=${DOCKER_SOCK#unix://}
export DOCKER_SOCK=${DOCKER_SOCK:-/var/run/docker.sock}
COMPOSE=(docker compose -p carbonate-pilot -f "$NETWORK/compose/compose-test-net.yaml" -f "$NETWORK/compose/docker/docker-compose-test-net.yaml" -f "$NETWORK/compose/databases.yaml")
ORDERER_CA="$NETWORK/organizations/ordererOrganizations/carbonate.local/tlsca/tlsca.carbonate.local-cert.pem"
ORDERER_TLS="$NETWORK/organizations/ordererOrganizations/carbonate.local/orderers/orderer.carbonate.local/tls"
ORDERER_ARGS=(-o localhost:7050 --ordererTLSHostnameOverride orderer.carbonate.local --tls --cafile "$ORDERER_CA")

retry() {
  local attempt
  for attempt in {1..30}; do
    if "$@"; then return 0; fi
    sleep 2
  done
  echo "Failed after 30 attempts: $*" >&2
  return 1
}

peer_env() {
  local org=$1 role=${2:-lite} domain
  if [[ "$org" == platform ]]; then
    domain=platform.carbonate.local
    export CORE_PEER_LOCALMSPID="$PLATFORM_MSP"
    export CORE_PEER_ADDRESS=localhost:7051
    [[ "$role" != commit ]] || export CORE_PEER_ADDRESS=localhost:7061
  elif [[ "$org" == verifier ]]; then
    domain=verifier.carbonate.local
    export CORE_PEER_LOCALMSPID="$VERIFIER_MSP"
    export CORE_PEER_ADDRESS=localhost:9051
    [[ "$role" != commit ]] || export CORE_PEER_ADDRESS=localhost:9061
  else
    echo "Unknown organisation: $org" >&2; return 1
  fi
  export CORE_PEER_TLS_ROOTCERT_FILE="$NETWORK/organizations/peerOrganizations/$domain/tlsca/tlsca.$domain-cert.pem"
  export CORE_PEER_MSPCONFIGPATH="$NETWORK/organizations/peerOrganizations/$domain/users/Admin@$domain/msp"
}

preflight() {
  local cmd failed=0
  for cmd in docker go jq peer cryptogen configtxgen osnadmin; do
    if ! command -v "$cmd" >/dev/null; then echo "Missing prerequisite: $cmd" >&2; failed=1; fi
  done
  if ! docker info >/dev/null 2>&1; then echo "Docker daemon is unavailable" >&2; failed=1; fi
  if ! docker compose version >/dev/null 2>&1; then echo "Docker Compose v2 is required" >&2; failed=1; fi
  [[ "$failed" == 0 ]] || return 1
  "${COMPOSE[@]}" config --quiet
  echo "Prerequisites found. Drunix documents Docker <=28; validate newer engines before pilot acceptance."
}

up() {
  rm -f "$NETWORK/.deployment-verified"
  preflight
  mkdir -p "$NETWORK/channel-artifacts"
  local config
  if [[ ! -d "$NETWORK/organizations/peerOrganizations" && ! -d "$NETWORK/organizations/ordererOrganizations" ]]; then
    for config in org1 org2 orderer; do
      cryptogen generate --config="$NETWORK/organizations/cryptogen/crypto-config-$config.yaml" --output="$NETWORK/organizations"
    done
  fi
  # Existing or partially generated identities are never silently replaced.
  [[ -f "$ORDERER_CA" ]]
  [[ -d "$NETWORK/organizations/peerOrganizations/platform.carbonate.local/msp" ]]
  [[ -d "$NETWORK/organizations/peerOrganizations/verifier.carbonate.local/msp" ]]
  "${COMPOSE[@]}" up -d carbonate-db-platform carbonate-db-verifier carbonate-keydb-platform carbonate-keydb-verifier
  local org
  for org in platform verifier; do
    retry "${COMPOSE[@]}" exec -T "carbonate-db-$org" /home/yugabyte/bin/ysqlsh -h "carbonate-db-$org" -p 5433 -U yugabyte -d yugabyte -c 'SELECT 1'
    retry "${COMPOSE[@]}" exec -T "carbonate-keydb-$org" keydb-cli ping
  done
  "${COMPOSE[@]}" up -d
  if [[ ! -f "$NETWORK/channel-artifacts/$CHANNEL_NAME.block" ]]; then
    FABRIC_CFG_PATH="$NETWORK/configtx" configtxgen -profile ChannelUsingRaft -channelID "$CHANNEL_NAME" -outputBlock "$NETWORK/channel-artifacts/$CHANNEL_NAME.block"
  fi
  if ! osnadmin channel list --channelID "$CHANNEL_NAME" -o localhost:7053 --ca-file "$ORDERER_CA" --client-cert "$ORDERER_TLS/server.crt" --client-key "$ORDERER_TLS/server.key" | grep -q '"name": "carbonate"'; then
    retry osnadmin channel join --channelID "$CHANNEL_NAME" --config-block "$NETWORK/channel-artifacts/$CHANNEL_NAME.block" -o localhost:7053 --ca-file "$ORDERER_CA" --client-cert "$ORDERER_TLS/server.crt" --client-key "$ORDERER_TLS/server.key"
  fi
  local role
  for org in platform verifier; do
    for role in commit lite; do
      peer_env "$org" "$role"
      if ! peer channel list | grep -qx "$CHANNEL_NAME"; then
        retry peer channel join -b "$NETWORK/channel-artifacts/$CHANNEL_NAME.block"
      fi
      retry peer channel getinfo -c "$CHANNEL_NAME"
    done
  done
}

deploy() {
  rm -f "$NETWORK/.deployment-verified"
  preflight
  local image
  for image in "$CC_BUILDER_IMAGE" "$CC_RUNTIME_IMAGE"; do
    docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
  done
  (cd "$CARBONATE_ROOT/chaincode" && go test ./... && go mod vendor)
  cd "$NETWORK"
  peer lifecycle chaincode package "$CC_NAME.tar.gz" --path "$CARBONATE_ROOT/chaincode" --lang golang --label "${CC_NAME}_${CC_VERSION}"
  local package_id org
  package_id=$(peer lifecycle chaincode calculatepackageid "$CC_NAME.tar.gz")
  local definition=(--channelID "$CHANNEL_NAME" --name "$CC_NAME" --version "$CC_VERSION" --sequence "$CC_SEQUENCE" --signature-policy "$CC_END_POLICY")
  # A committed sequence cannot be checked/approved again on Drunix.
  # Reuse it only when both organisations already have this exact code package.
  peer_env platform commit
  if peer lifecycle chaincode querycommitted -C "$CHANNEL_NAME" -n "$CC_NAME" --output json | jq -e --arg v "$CC_VERSION" --argjson s "$CC_SEQUENCE" --arg policy "$CC_POLICY_BASE64" '.version == $v and .sequence == $s and .validation_parameter == $policy' >/dev/null; then
    for org in platform verifier; do
      peer_env "$org" lite
      peer lifecycle chaincode queryinstalled --output json | jq -e --arg id "$package_id" 'any(.installed_chaincodes[]?; .package_id == $id)' >/dev/null
      peer_env "$org" commit
      peer lifecycle chaincode queryapproved -C "$CHANNEL_NAME" -n "$CC_NAME" --output json | jq -e --arg id "$package_id" --arg v "$CC_VERSION" --argjson s "$CC_SEQUENCE" '.version == $v and .sequence == $s and .source.Type.LocalPackage.package_id == $id' >/dev/null
    done
    verify
    return
  fi
  for org in platform verifier; do
    peer_env "$org" lite
    if ! peer lifecycle chaincode queryinstalled --output json | jq -e --arg id "$package_id" 'any(.installed_chaincodes[]?; .package_id == $id)' >/dev/null; then
      peer lifecycle chaincode install "$CC_NAME.tar.gz"
    fi
    peer_env "$org" commit
    # Reuse an existing approval for this exact package/version/sequence.
    # Readiness below also checks that its policy matches the desired definition.
    if ! peer lifecycle chaincode queryapproved -C "$CHANNEL_NAME" -n "$CC_NAME" --output json | jq -e --arg id "$package_id" --arg v "$CC_VERSION" --argjson s "$CC_SEQUENCE" '.version == $v and .sequence == $s and .source.Type.LocalPackage.package_id == $id' >/dev/null; then
      peer lifecycle chaincode approveformyorg "${ORDERER_ARGS[@]}" "${definition[@]}" --package-id "$package_id"
    fi
  done
  ready() {
    peer lifecycle chaincode checkcommitreadiness "${definition[@]}" --output json | jq -e '.approvals.CarbonCreditPlatformMSP == true and .approvals.VerifierMSP == true' >/dev/null
  }
  retry ready
  if ! peer lifecycle chaincode querycommitted -C "$CHANNEL_NAME" -n "$CC_NAME" --output json | jq -e --arg v "$CC_VERSION" --argjson s "$CC_SEQUENCE" '.version == $v and .sequence == $s' >/dev/null; then
    peer lifecycle chaincode commit "${ORDERER_ARGS[@]}" "${definition[@]}" \
      --peerAddresses localhost:7061 --tlsRootCertFiles "$NETWORK/organizations/peerOrganizations/platform.carbonate.local/tlsca/tlsca.platform.carbonate.local-cert.pem" \
      --peerAddresses localhost:9061 --tlsRootCertFiles "$NETWORK/organizations/peerOrganizations/verifier.carbonate.local/tlsca/tlsca.verifier.carbonate.local-cert.pem"
  fi
  verify
}

verify() {
  # Discard a stale success marker before performing current checks.
  rm -f "$NETWORK/.deployment-verified"
  local org
  for org in platform verifier; do
    peer_env "$org" lite
    retry verify_definition
    retry verify_query
  done
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$NETWORK/.deployment-verified"
  echo "Carbonate chaincode committed and queried successfully by both organisations. No business data was seeded."
}
verify_definition() {
  mkdir -p "$NETWORK/channel-artifacts"
  peer lifecycle chaincode querycommitted -C "$CHANNEL_NAME" -n "$CC_NAME" --output json | tee "$NETWORK/channel-artifacts/$CORE_PEER_LOCALMSPID-definition.json" | jq -e --arg v "$CC_VERSION" --argjson s "$CC_SEQUENCE" --arg policy "$CC_POLICY_BASE64" '.version == $v and .sequence == $s and .validation_parameter == $policy and .approvals.CarbonCreditPlatformMSP == true and .approvals.VerifierMSP == true' >/dev/null
}
verify_query() {
  peer chaincode query -C "$CHANNEL_NAME" -n "$CC_NAME" -c '{"Args":["GetPilotInfo"]}' | tee "$NETWORK/channel-artifacts/$CORE_PEER_LOCALMSPID-pilot-info.json" | jq -e --arg m "$CORE_PEER_LOCALMSPID" --arg v "$CC_VERSION" '.name == "carbonate" and .version == $v and .channel == "carbonate" and .clientMSP == $m' >/dev/null
}

case "${1:-help}" in
  preflight) preflight ;;
  up) up ;;
  deploy) deploy ;;
  start) up; deploy ;;
  verify) preflight; verify ;;
  status) "${COMPOSE[@]}" ps ;;
  stop) "${COMPOSE[@]}" stop ;;
  *) echo "Usage: $0 {preflight|up|deploy|start|verify|status|stop}"; [[ "${1:-help}" == help ]] ;;
esac
