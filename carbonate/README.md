# Carbonate pilot preparation

This directory contains the Carbonate pilot implementation and validation flow. The network and bootstrap chaincode have passed live deployment verification in this checkout. It supports generation evidence, linked corrections and signed verifier decisions; credit issuance and trading are outside this step.

| Component | Location | Status |
| --- | --- | --- |
| Chaincode | `chaincode/` | Generation records, packages and signed verifier decisions; version `0.2.0` |
| Network | `network/`, `scripts/network.sh` | Two application organisations, one channel, local Fabric topology |
| Application API | `api/` | Python service, operator CLI and durable publication outbox |
| Connectors | `connectors/` | Deterministic mock solar response connector |
| Application database | `database/` | Private SQLite farmer/installation/meter and evidence schema |
| Portals | `portals/` | Reserved, not implemented |
| Documentation | `docs/` | Validation, workflow, and supporting pilot documentation |

The application organisations are `CarbonCreditPlatformMSP` and `VerifierMSP` on channel `carbonate`. `OrdererMSP` remains the ordering-service identity, not a third application organisation. Chaincode `carbonate`, version `0.2.0`, sequence `2` requires endorsement by both organisations. Cryptogen identities are generated locally; sample certificates are never copied into the Carbonate deployment.

From the repository root:

```bash
./carbonate/scripts/network.sh preflight
./carbonate/scripts/network.sh start
./carbonate/scripts/network.sh verify
./carbonate/scripts/network.sh status
./carbonate/scripts/network.sh stop
```

`start` creates identities, starts the Carbonate network and storage services, creates/joins the channel, packages Carbonate chaincode, installs on both lite peers, approves through both committing peers, commits and queries both organisations. It creates `network/.deployment-verified` only after live checks pass. `stop` preserves containers and volumes. There is no destructive teardown command.

Requirements: Docker with Compose v2, the `peer`, `cryptogen`, `configtxgen`, and `osnadmin` CLI tools in `.tools/bin/`, and `jq`. This checkout has ignored local tooling under `.tools/`; the deployment runner uses only Carbonate-local tools and does not build from a parent repository. Drunix documents Linux and Docker <=28. macOS/ARM and newer Docker engines require actual runtime validation. The chaincode has its own Go module and can be tested independently:

```bash
(cd carbonate/chaincode && go test ./...)
```

The runner supports paths containing spaces. Ports are bound to localhost but retain upstream numbers (7050/7053, 7051/7061/7071, 9051/9061/9071 and storage/metrics ports). An existing service occupying those ports must be moved or stopped separately. Container names, network and volumes are Carbonate-specific. No original test-network teardown or global Docker cleanup is called.

Read [validation](docs/validation.md), [configuration provenance](docs/configuration.md), [reference audit](docs/reference-audit.md), and [source-tree pruning](docs/deletion-plan.md). The adapted network configuration and required upstream license and notice are kept with Carbonate. Fabric service images remain external container dependencies.

Run `python3 -m carbonate.api.live_flow` from the repository root for the explicit Rohan-to-verifier acceptance workflow. See [workflow documentation](docs/workflow.md) for privacy boundaries, commands and recovery. Network deployment does not seed application data.
