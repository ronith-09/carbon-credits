#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
CARBONATE_ROOT=$(cd "$(dirname "$0")/.." && pwd)
export PATH="$CARBONATE_ROOT/.tools/go/bin:$CARBONATE_ROOT/.tools/bin:$PATH"
bash -n "$CARBONATE_ROOT/scripts/network.sh"
(cd "$CARBONATE_ROOT/chaincode" && go test ./...)
export DOCKER_SOCK=/var/run/docker.sock
config=$(mktemp)
trap 'rm -f "$config"' EXIT
docker compose -p carbonate-pilot -f "$CARBONATE_ROOT/network/compose/compose-test-net.yaml" -f "$CARBONATE_ROOT/network/compose/docker/docker-compose-test-net.yaml" -f "$CARBONATE_ROOT/network/compose/databases.yaml" config --format json > "$config"
python3 - "$config" "$CARBONATE_ROOT" <<'PY'
import json, sys
from pathlib import Path
config = json.loads(Path(sys.argv[1]).read_text())
services = config['services']
expected = {'CarbonCreditPlatformMSP': 3, 'VerifierMSP': 3}
actual = {}
for service in services.values():
    env = service.get('environment', {})
    msp = env.get('CORE_PEER_LOCALMSPID')
    if msp:
        actual[msp] = actual.get(msp, 0) + 1
        assert env.get('CORE_VM_DOCKER_HOSTCONFIG_NETWORKMODE') == 'carbonate_pilot'
    for port in service.get('ports', []):
        assert port.get('host_ip') == '127.0.0.1', port
assert actual == expected, actual
assert len(services) == 11, len(services)
assert config['networks']['test']['name'] == 'carbonate_pilot'
root = Path(sys.argv[2])
for rel in ['network/network.config', 'scripts/network.sh', 'chaincode/main.go', 'network/configtx/configtx.yaml']:
    source = (root / rel).read_text()
    for banned in ['Org1MSP', 'Org2MSP', 'InitLedger', 'GetAllAssets', 'asset-transfer-basic', 'asset-transfer-private-data']:
        assert banned not in source, (rel, banned)
print('PASS: two MSPs, isolated network, localhost endpoints, no sample deployment references')
PY
