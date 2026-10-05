"""Farmer-to-verifier workflow. SQLite owns PII; only explicit DTOs reach Fabric."""
import datetime as dt
import json
import sqlite3
import uuid
from decimal import Decimal, InvalidOperation
from .storage import PrivateStorage, ROOT, sha256
from .ledger import LedgerError, decision_payload

class ValidationError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def new_id(prefix):
    return prefix + "-" + uuid.uuid4().hex


def timestamp(value):
    if not isinstance(value, str):
        raise ValidationError("timestamp must be an RFC3339 string")
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError("invalid timestamp") from error
    if result.tzinfo is None or result.microsecond:
        raise ValidationError("timestamp requires a timezone and whole seconds")
    return result.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CarbonateService:
    def __init__(self, ledger, private_root=None):
        self.ledger = ledger
        self.storage = PrivateStorage(private_root or ROOT / ".private")
        self.db = self.storage.connect()

    def close(self):
        self.db.close()

    def company(self):
        if self.ledger.org != "platform":
            raise PermissionError("company action requires CarbonCreditPlatformMSP")

    def register(self, farmer_name, source_meter_id, farmer_details=None, installation_details=None):
        self.company()
        if not farmer_name.strip() or not source_meter_id.strip():
            raise ValidationError("farmer name and source meter ID are required")
        farmer_id, installation_id, meter_id = new_id("farmer"), new_id("installation"), new_id("meter")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("INSERT INTO farmers VALUES(?,?,?)", (farmer_id,farmer_name,json.dumps(farmer_details or {})))
            self.db.execute("INSERT INTO installations VALUES(?,?,?,?)", (installation_id,farmer_id,"solar",json.dumps(installation_details or {})))
            self.db.execute("INSERT INTO meters VALUES(?,?,?)", (meter_id,installation_id,source_meter_id))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return dict(farmerId=farmer_id,installationId=installation_id,meterRef=meter_id)

    def seed_rohan(self):
        self.company()
        found = self.db.execute("SELECT m.id meterRef,i.id installationId,f.id farmerId FROM meters m JOIN installations i ON m.installation_id=i.id JOIN farmers f ON i.farmer_id=f.id WHERE m.source_meter_id=?", ("MOCK-ROHAN-SOLAR-001",)).fetchone()
        if found:
            return dict(found)
        return self.register("Rohan", "MOCK-ROHAN-SOLAR-001", {"testData":True}, {"testData":True,"technology":"solar PV"})

    def enqueue(self, kind, entity_id, args):
        self.db.execute("INSERT INTO outbox VALUES(?,?,?,?,?,NULL)", (new_id("event"),kind,entity_id,json.dumps(args),"pending"))

    def import_reading(self, raw, previous_id=None):
        self.company()
        evidence_hash = self.storage.put(raw)
        intake_id = new_id("intake")
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        self.db.execute("INSERT INTO intakes VALUES(?,?,?,?,NULL)", (intake_id,evidence_hash,now,"received"))
        begun = False
        try:
            reading = json.loads(raw, parse_float=Decimal)
            source_id = reading["sourceId"]
            if not isinstance(source_id,str) or not source_id or len(source_id)>256:
                raise ValidationError("invalid source ID")
            start, end, received = [timestamp(reading[key]) for key in ("measurementStart","measurementEnd","receivedTimestamp")]
            if start >= end or received < end:
                raise ValidationError("invalid measurement or received period")
            mwh = Decimal(str(reading["MWh"]))
            if not mwh.is_finite() or mwh<=0 or mwh>Decimal("1000000"):
                raise ValidationError("MWh must be finite, positive and within pilot limits")
            self.db.execute("BEGIN IMMEDIATE"); begun = True
            if self.db.execute("SELECT 1 FROM generation_records WHERE source_id=?", (source_id,)).fetchone():
                raise ValidationError("duplicate source ID")
            meter = self.db.execute("SELECT id FROM meters WHERE source_meter_id=?", (reading["meterId"],)).fetchone()
            if meter is None:
                raise ValidationError("unregistered meter")
            version = 1
            if previous_id:
                previous = self.db.execute("SELECT r.*,o.state FROM generation_records r JOIN outbox o ON o.entity_id=r.id WHERE r.id=?",(previous_id,)).fetchone()
                if not previous or previous["meter_id"]!=meter["id"] or previous["state"]!="done":
                    raise ValidationError("correction requires a published predecessor for the same meter")
                if self.db.execute("SELECT 1 FROM generation_records WHERE previous_id=?", (previous_id,)).fetchone():
                    raise ValidationError("correction predecessor already has a successor")
                on_chain = self.ledger.query("GetGenerationRecord",previous_id)
                if on_chain["status"] != "accepted":
                    raise ValidationError("correction predecessor is not active")
                package_id = on_chain.get("packageId", "")
                if package_id:
                    package = self.ledger.query("GetEvidencePackage",package_id)
                    if package["status"] not in ("corrections_requested","rejected"):
                        raise ValidationError("packaged evidence is locked")
                version = previous["version"]+1
            overlap = self.db.execute("""SELECT r.id FROM generation_records r WHERE r.meter_id=? AND r.id!=?
              AND r.measurement_start < ? AND r.measurement_end > ?
              AND NOT EXISTS (SELECT 1 FROM generation_records child WHERE child.previous_id=r.id) LIMIT 1""",(meter["id"],previous_id or "",end,start)).fetchone()
            if overlap:
                raise ValidationError("overlapping active period for meter")
            record_id = new_id("generation")
            record = dict(generationRecordId=record_id,meterRef=meter["id"],measurementStart=start,measurementEnd=end,evidenceHash=evidence_hash,
                          sourceRef=sha256(("mock-solar\0"+source_id).encode()),version=version,previousGenerationRecordId=previous_id or "",status="accepted",supersededBy="",packageId="")
            self.db.execute("INSERT INTO generation_records VALUES(?,?,?,?,?,?,?,?,?,?,?)",(record_id,source_id,meter["id"],start,end,str(mwh),received,evidence_hash,version,previous_id,intake_id))
            self.enqueue("generation",record_id,[json.dumps(record)])
            self.db.execute("UPDATE intakes SET outcome='accepted' WHERE id=?",(intake_id,))
            self.db.execute("COMMIT"); begun=False
        except (KeyError, TypeError, json.JSONDecodeError, InvalidOperation) as error:
            if begun: self.db.execute("ROLLBACK")
            self.db.execute("UPDATE intakes SET outcome='rejected',error=? WHERE id=?",(str(error),intake_id))
            raise ValidationError("invalid mock solar response") from error
        except Exception as error:
            if begun: self.db.execute("ROLLBACK")
            self.db.execute("UPDATE intakes SET outcome='rejected',error=? WHERE id=?",(str(error),intake_id))
            raise
        # Import identity/evidence are durable before the external ledger call.
        self.sync()
        return record

    def package(self, record_ids, previous_package_id=None):
        self.company()
        if not record_ids or len(set(record_ids)) != len(record_ids) or len(record_ids)>1000:
            raise ValidationError("package requires 1 to 1000 unique records")
        if previous_package_id:
            old = self.ledger.query("GetEvidencePackage",previous_package_id)
            if old["status"] not in ("corrections_requested","rejected"):
                raise ValidationError("previous package is not open for revision")
        manifest = {"schema":"CarbonateEvidencePackage/v1", "previousPackageId":previous_package_id or "", "records":[]}
        for record_id in sorted(record_ids):
            row = self.db.execute("SELECT r.*,o.state FROM generation_records r JOIN outbox o ON o.entity_id=r.id WHERE r.id=?",(record_id,)).fetchone()
            if row is None or row["state"]!="done":
                raise ValidationError("all package records must be published")
            record = self.ledger.query("GetGenerationRecord",record_id)
            if record["status"]!="accepted" or (record.get("packageId") and record["packageId"]!=previous_package_id):
                raise ValidationError("record is superseded or already packaged")
            self.storage.get(row["evidence_hash"])
            manifest["records"].append({"generationRecordId":record_id,"evidenceHash":row["evidence_hash"],"version":row["version"],"meterRef":row["meter_id"],"MWh":row["mwh"],"measurementStart":row["measurement_start"],"measurementEnd":row["measurement_end"]})
        package_hash = self.storage.put(canonical(manifest))
        package_id = new_id("package")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("INSERT INTO evidence_packages VALUES(?,?,?,?)",(package_id,package_hash,json.dumps(sorted(record_ids)),previous_package_id))
            self.enqueue("package",package_id,[package_id,json.dumps(sorted(record_ids)),package_hash,previous_package_id or ""])
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK"); raise
        self.sync()
        return self.ledger.query("GetEvidencePackage",package_id)

    def decide(self, package_id, outcome, reason):
        if self.ledger.org!="verifier":
            raise PermissionError("only VerifierMSP may sign decisions")
        if outcome not in ("approved","rejected","corrections_requested"):
            raise ValidationError("invalid decision outcome")
        package = self.ledger.query("GetEvidencePackage",package_id)
        if package["status"]!="submitted":
            raise ValidationError("package already has a decision")
        reason_hash = self.storage.put(reason.encode())
        payload = decision_payload(package_id,package["evidenceHash"],outcome,reason_hash)
        signature = self.ledger.sign(payload)
        # One durable signature per decision; recovery never creates another decision.
        self.enqueue("decision", "decision-"+package_id, [package_id,outcome,reason_hash,signature])
        self.sync()
        return self.ledger.query("GetDecision",package_id)

    def sync(self):
        pending = self.db.execute("SELECT * FROM outbox WHERE state='pending' ORDER BY rowid").fetchall()
        for event in pending:
            # One local database can contain both orgs' work; each operator publishes only its own.
            if (event["kind"]=="decision") != (self.ledger.org=="verifier"):
                continue
            args = json.loads(event["args_json"])
            if event["kind"]=="generation":
                query, method, entity = "GetGenerationRecord","SubmitGeneration",event["entity_id"]
                expected = json.loads(args[0])
            elif event["kind"]=="package":
                query, method, entity = "GetEvidencePackage","SubmitEvidencePackage",event["entity_id"]
                expected = {"packageId":args[0],"generationRecordIds":json.loads(args[1]),"evidenceHash":args[2],"previousPackageId":args[3]}
            else:
                query, method, entity = "GetDecision","RecordDecision",args[0]
                expected = {"packageId":args[0],"outcome":args[1],"reasonHash":args[2],"signature":args[3]}
            try:
                existing = self.ledger.find(query,entity)
                if existing is None:
                    self.ledger.submit(method,*args)
                    existing = self.ledger.query(query,entity)
                for key,value in expected.items():
                    if key in ("status","supersededBy","packageId") and event["kind"]=="generation":
                        continue
                    if existing.get(key)!=value:
                        raise LedgerError("committed evidence differs from durable publication event")
                self.db.execute("UPDATE outbox SET state='done',last_error=NULL WHERE id=?",(event["id"],))
            except Exception as error:
                self.db.execute("UPDATE outbox SET last_error=? WHERE id=?",(str(error),event["id"]))
                raise LedgerError(f"publication pending for {event['entity_id']}; run sync after resolving: {error}") from error
