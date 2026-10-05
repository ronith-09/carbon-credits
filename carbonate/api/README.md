# Carbonate application service

`CarbonateService` implements farmer/installation/meter registration, raw solar imports, linked corrections, private evidence packages and signed verifier decisions. It is exposed through a local operator CLI, not an unauthenticated HTTP server.

From the repository root:

```bash
python3 -m carbonate.api --help
python3 -m unittest discover -s carbonate/api/tests -v
python3 -m carbonate.api.live_flow
```

See [workflow rules and commands](../docs/workflow.md) and [validation results](../docs/validation.md). `ledger.py` uses the existing Drunix CLI and selected MSP identity with TLS, endorsements from both organisations and commit-status waiting. Private evidence and application state live under ignored `.private/` by default. The live test explicitly creates Rohan test data; deployment itself does not seed records.
