# Generation evidence and verification pilot

The application service is a local Python API with an operator CLI. It uses a separate SQLite application database, a private content-addressed evidence directory, and the existing two-MSP Carbonate channel. There is no HTTP server or external solar integration in this step. No credit serials, sales, payouts or AI are implemented.

## Data boundaries

| Private application storage | Public ledger |
| --- | --- |
| Farmer name/details; installation; external meter ID | Opaque meter reference |
| Exact solar API response bytes, MWh and received timestamp | Generation ID, UTC period, SHA-256 evidence hash |
| Raw provider source event ID | Domain-separated SHA-256 source reference |
| All original/corrected response objects and database version links | Version and predecessor/successor links; effective status |
| Canonical evidence-package manifest and decision reason | Package hash/membership; signed decision and reason hash |

The default private directory is `carbonate/.private`, ignored by Git and restricted to the OS user (`0700` directories, `0600` database, `0400` evidence objects). Objects are atomically published under their SHA-256 digest and never overwritten by the service. Database triggers reject generation/package updates and deletions. This is private local pilot storage, not encrypted multi-tenant storage or regulatory WORM storage. It is not mounted into peers or containers. Local operators on this test machine have access to the existing development MSP identities; the CLI does not expose signing keys over HTTP.

## Record and decision rules

- A source ID identifies one provider event. Replays are rejected even if their bytes differ. A correction requires a **new unique source event ID**, a new generation ID and `--corrects` pointing to the current predecessor. It increments the version exactly once and retains both raw responses.
- The mock source reference is `SHA256("mock-solar" + NUL + sourceId)`. External IDs and PII are never sent to chaincode.
- MWh must be finite and positive. Periods are canonical UTC with whole seconds and use half-open `[start,end)` boundaries; adjacent periods are allowed. Received time must be at or after measurement end.
- SQLite `BEGIN IMMEDIATE` serializes imports and reserves pending periods. Chaincode checks duplicates and overlaps independently. Per-meter index and source keys participate in ledger read/write validation to prevent races.
- New accepted records may be corrected before packaging. Once packaged they are locked. A verifier's `corrections_requested` or `rejected` decision allows linked revisions and a new package; an approved package stays locked.
- Packages contain active published records and a hash of a private canonical manifest. A revised package links to its predecessor. Signing a decision closes that package permanently; another decision requires another package.
- Only `CarbonCreditPlatformMSP` can submit generation records/packages. Only `VerifierMSP` can record a decision. Either MSP can query records/packages/decisions.
- Decisions are `corrections_requested`, `rejected`, or `approved`. They contain an ECDSA/SHA-256 detached signature checked against the submitting verifier's X.509 certificate, plus the Fabric transaction ID and timestamp. Fabric MSP validation establishes the certificate's organisation. The signature payload is exactly these newline-separated values, with **no trailing newline**:

```text
CarbonateVerification/v1
carbonate
<packageId>
<packageHash>
<outcome>
<reasonHash>
```

## Run the local workflow

Run all commands from the repository root. Python 3, OpenSSL and the Fabric CLI tooling are required. The existing channel must have chaincode version `0.2.0`, sequence `2` deployed.

```bash
python3 -m carbonate.api seed-rohan
python3 -m carbonate.api import carbonate/connectors/fixtures/rohan-reading.json
python3 -m carbonate.api package <generationRecordId>
python3 -m carbonate.api --org verifier decide <packageId> corrections_requested --reason-file <private-reason-file>
python3 -m carbonate.api import carbonate/connectors/fixtures/rohan-correction.json --corrects <originalGenerationRecordId>
python3 -m carbonate.api package <correctedGenerationRecordId> --previous-package <originalPackageId>
python3 -m carbonate.api --org verifier decide <revisedPackageId> approved --reason-file <private-reason-file>
python3 -m carbonate.api --org platform query decision <revisedPackageId>
python3 -m carbonate.api --org verifier query decision <revisedPackageId>
```

Both decision query commands also verify the detached signature. To register another test farmer/installation/meter, use `python3 -m carbonate.api register --name <name> --source-meter-id <meter-id>`. Personal/installation detail objects are accepted through `CarbonateService.register`; they remain in SQLite.

For a complete, repeatable acceptance run, use:

```bash
python3 -m carbonate.api.live_flow
```

This explicitly seeds Rohan, publishes a reading, exercises duplicate/overlap and MSP denial paths, requests corrections, publishes a linked correction, approves its revised package, and rejects a separate adjacent reading's package. It checks both organisations' queries and independent signature verification. The run preserves all test data and reuses its provider IDs on reruns; it never resets the network or database.

## Publication recovery

An accepted import and its exact ledger arguments are committed to SQLite before submission. The durable outbox keeps failures pending. A retry queries the existing generation/package/decision and compares its immutable fields; a transaction that committed before its acknowledgement was lost is not resubmitted. Pending records cannot be packaged. Source IDs and periods remain reserved while publication is pending.

```bash
python3 -m carbonate.api --org platform sync
python3 -m carbonate.api --org verifier sync
```

Do not delete a pending event or change its evidence to repair an error. Resolve the reported ledger/connection issue and replay it. A permanent conflict introduced outside this application requires operator reconciliation; the tool leaves its evidence and failure details intact rather than claiming publication succeeded.

A verifier must inspect the private evidence package/raw readings through an authorised off-ledger handoff before choosing a decision. The mock acceptance run performs this handoff locally. Authentication/portal access, encrypted remote evidence exchange and production storage are future integration work.
