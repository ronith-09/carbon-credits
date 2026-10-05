PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS farmers (id TEXT PRIMARY KEY, name TEXT NOT NULL, details_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS installations (id TEXT PRIMARY KEY, farmer_id TEXT NOT NULL REFERENCES farmers(id), kind TEXT NOT NULL CHECK(kind='solar'), details_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meters (id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(id), source_meter_id TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS intakes (id TEXT PRIMARY KEY, raw_hash TEXT NOT NULL, received_at TEXT NOT NULL, outcome TEXT NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS generation_records (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL UNIQUE, meter_id TEXT NOT NULL REFERENCES meters(id),
 measurement_start TEXT NOT NULL, measurement_end TEXT NOT NULL, mwh TEXT NOT NULL,
 received_at TEXT NOT NULL, evidence_hash TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
 previous_id TEXT UNIQUE REFERENCES generation_records(id), intake_id TEXT NOT NULL REFERENCES intakes(id),
 CHECK(measurement_start < measurement_end)
);
CREATE INDEX IF NOT EXISTS generation_meter_period ON generation_records(meter_id,measurement_start,measurement_end);
CREATE TABLE IF NOT EXISTS evidence_packages (id TEXT PRIMARY KEY, evidence_hash TEXT NOT NULL, record_ids_json TEXT NOT NULL, previous_id TEXT REFERENCES evidence_packages(id));
CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, kind TEXT NOT NULL, entity_id TEXT NOT NULL UNIQUE, args_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','done')), last_error TEXT);
CREATE TRIGGER IF NOT EXISTS immutable_generation_update BEFORE UPDATE ON generation_records BEGIN SELECT RAISE(ABORT,'generation evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_generation_delete BEFORE DELETE ON generation_records BEGIN SELECT RAISE(ABORT,'generation evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_package_update BEFORE UPDATE ON evidence_packages BEGIN SELECT RAISE(ABORT,'package evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_package_delete BEFORE DELETE ON evidence_packages BEGIN SELECT RAISE(ABORT,'package evidence is immutable'); END;
PRAGMA user_version=1;
