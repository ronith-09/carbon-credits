# Mock solar connector

`MockSolarAPI.fetch(path)` returns the exact response bytes of a deterministic mock API fixture. It does not normalize or reserialize evidence before hashing it.

Fixtures provide `sourceId`, `meterId`, `measurementStart`, `measurementEnd`, `MWh` and `receivedTimestamp`. `rohan-correction.json` uses a new unique source event ID and is imported with an explicit predecessor generation ID. The application preserves all original/corrected bytes privately, including the correction explanation, while publishing only hashes and opaque references.

This is a mock transport; live-provider authentication, polling and remote API access are not part of this step.
