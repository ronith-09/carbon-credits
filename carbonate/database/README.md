# Carbonate application database

`migrations/001_generation.sql` creates the pilot SQLite application database. This is separate from both Drunix/Yugabyte ledger state databases.

Tables hold farmers, installations, registered meters, immutable generation versions, immutable evidence package metadata, intake audit entries and a durable publication outbox. Unique source IDs and predecessor links plus serialized import transactions reject replays, correction forks and overlapping active/pending periods. Generation/package evidence rows cannot be updated or deleted through SQL; status/successor changes are projections and ledger queries.

The database is created as `.private/application.sqlite3` with mode `0600`. Exact raw responses, correction responses, package manifests and reason documents are stored as immutable content-addressed files in the adjacent private `objects/` directory. Back up the database and objects together. No PII or MWh is written to chaincode.
