# Configuration provenance

The Carbonate Compose, cryptogen, peer core and channel configuration files were adapted from the Drunix Fabric test network before the Drunix source tree was removed from this checkout. Carbonate keeps the adapted configuration, existing copyright and SPDX headers, and the upstream Fabric license and notice under `licenses/upstream-fabric/`. The original source files are no longer included.

Changes in the Carbonate copies:

- Org1/Org2 MSP identities become `CarbonCreditPlatformMSP` / `VerifierMSP`, including all channel policies and peer identities.
- Domains become `platform.carbonate.local`, `verifier.carbonate.local`, and `carbonate.local` for the orderer.
- Two application MSPs share channel `carbonate`; `OrdererMSP` provides ordering.
- Both organisations' committing peers are anchor peers in the genesis config.
- The original Org2 Writers rule incorrectly also named Org1's member policy. Carbonate's Verifier Writers rule uses only Verifier identities.
- Containers, Docker network and storage have Carbonate-specific names; endpoints bind to localhost.
- KeyDB is pinned to the inspected upstream image digest. Yugabyte and Drunix retain upstream image tags.
- The chaincode builder/runtime are external images `npcioss/drunix-ccenv:1.0` and `npcioss/drunix-baseos:1.0`; the runner pulls them before installation because the peer does not fetch a missing builder automatically.
- The runner uses explicit Carbonate lifecycle parameters and checks both approvals, committed version/sequence and live queries. It does not use the sample network runner or its generic cleanup helpers.

The human-readable endorsement policy and its serialized `CC_POLICY_BASE64` are paired in `network/network.config`; update both and increment the lifecycle sequence when changing the policy. A source/package change also requires an intentional lifecycle upgrade.

Do not globally replace MSP strings in Drunix: the upstream framework's unit/integration tests and original samples still require their own fixtures.

Pilot acceptance still requires carbon-credit business rules, identity roles, project/evidence schemas, verification/issuance/retirement flows, API authorization, connector reconciliation, portal workflows and recovery tests. The bootstrap query does not establish that these workflows pass.
