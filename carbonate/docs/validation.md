# Validation

Validation is being performed against this checkout on macOS ARM64 with Docker 29.5.2. The Carbonate validation workflow is designed to confirm the local pilot setup and should be re-run in the target environment before broader rollout.

Completed checks:

- Built the local `peer`, `cryptogen`, `configtxgen` and `osnadmin` tools from the repository source using Go 1.26.1.
- `scripts/check.sh`: Go contract tests, Bash syntax, merged Compose configuration, exactly two peer MSPs with the expected service topology, isolated Docker network, localhost port bindings, and no sample chaincode references in deployment inputs.
- Generated and inspected the actual channel genesis block: application members are exactly `CarbonCreditPlatformMSP` and `VerifierMSP`.
- Started both SQL stores, both KeyDB services, the orderer and both organisations' LP/CP/VSCC services.
- Joined both committing peers and both lite peers to `carbonate`.
- `git diff --exit-code` confirms existing tracked files remain unchanged.

Live deployment **passed** at `2026-09-25T07:47:05Z`. Carbonate version `0.1.0`, sequence `1`, is installed and approved for both organisations and committed on `carbonate` with the two-organisation AND endorsement policy. `GetPilotInfo` returned the expected name, version, channel and caller MSP through both lite peers. The final complete `scripts/network.sh start` command exited 0 against the running network. An existing deployment is reused only after checking the committed policy/version/sequence and both organisations' installed and approved package IDs.

See [deployment-evidence.json](deployment-evidence.json) for the captured responses. Local raw logs are in the ignored `network/startup-final.log`, `network/verification.log`, and `network/deployment.log`. `network/.deployment-verified` records the last successful verification, not full pilot acceptance.

Runtime issues resolved in the Carbonate runner: database readiness uses the service DNS address; builder/runtime images are pulled explicitly; repeat startup reuses a committed sequence instead of re-approving it. No Carbonate deployment scripts were modified during this validation pass.

Full pilot business-workflow acceptance is pending implementation of the API, connectors, database projections, portals and credit lifecycle transactions. The project retains its sample references and framework assets as required; no Carbonate ledger data or deployment files were deleted.
