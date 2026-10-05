# Source-tree pruning

The Drunix framework and sample source tree are no longer included in this checkout. Carbonate keeps its own chaincode, API, connectors, database, deployment configuration, scripts, and documentation under `carbonate/`.

The network still uses the external `npcioss/drunix-*` container images. This checkout's local Fabric CLI executables and generated network state are stored under ignored Carbonate-local directories and were preserved during pruning. A fresh checkout must provide `peer`, `cryptogen`, `configtxgen`, and `osnadmin` in `.tools/bin/` before running the network scripts.

The historical [file inventory](deletion-list.csv) and [reference audit](reference-audit.md) describe the source tree before pruning; they are retained as provenance records, not as current workspace contents or build requirements.