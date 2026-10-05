"""Real two-MSP acceptance test. Creates only explicitly requested pilot test data.
Run: python3 -m carbonate.api.live_flow
The local evidence/database persists; reruns reuse the saved Rohan source events.
"""
import json
from .service import CarbonateService, ValidationError, canonical, new_id
from .storage import ROOT, sha256
from .ledger import FabricLedger, LedgerError, decision_payload, verify_decision


def main():
    platform=FabricLedger("platform"); verifier=FabricLedger("verifier")
    company=CarbonateService(platform); reviewer=CarbonateService(verifier)
    checks=[]
    def passed(name):
        checks.append(name);print("PASS: "+name,flush=True)
    def rejected(name, fragment, call):
        try:call()
        except (ValidationError,LedgerError,PermissionError) as error:
            if fragment not in str(error):raise
            passed(name)
        else:raise AssertionError("unexpectedly accepted: "+name)
    def imported(raw,previous=None):
        source=json.loads(raw)["sourceId"]
        row=company.db.execute("SELECT id FROM generation_records WHERE source_id=?",(source,)).fetchone()
        if row:
            company.sync();return platform.query("GetGenerationRecord",row[0])
        return company.import_reading(raw,previous)
    def packaged(id, previous=None):
        for row in company.db.execute("SELECT * FROM evidence_packages ORDER BY rowid"):
            if json.loads(row["record_ids_json"])==[id] and row["previous_id"]==previous:
                company.sync();return platform.query("GetEvidencePackage",row["id"])
        return company.package([id],previous)
    def decide(package,outcome,reason):
        found=verifier.find("GetDecision",package["packageId"])
        if found:
            assert found["outcome"]==outcome;return found
        reviewer.sync()
        return reviewer.decide(package["packageId"],outcome,reason)
    try:
        person=company.seed_rohan()
        assert company.db.execute("SELECT name FROM farmers WHERE id=?",(person["farmerId"],)).fetchone()[0]=="Rohan"
        passed("farmer Rohan, installation and registered meter exist only in application database")
        original=(ROOT/"connectors/fixtures/rohan-reading.json").read_bytes()
        correction=(ROOT/"connectors/fixtures/rohan-correction.json").read_bytes()
        first=imported(original)
        passed("mock solar response imported and generation record published")
        rejected("application rejects repeated source ID","duplicate source",lambda:company.import_reading(original))
        overlap=json.loads(original);overlap.update(sourceId="mock-rohan-overlap-probe",measurementStart="2026-09-20T06:30:00Z",measurementEnd="2026-09-20T07:30:00Z",receivedTimestamp="2026-09-20T08:00:00Z")
        rejected("application rejects overlapping active period","overlapping",lambda:company.import_reading(canonical(overlap)))
        duplicate=dict(first,generationRecordId=new_id("generation"),status="accepted",supersededBy="",packageId="")
        rejected("chaincode rejects duplicate source reference when API is bypassed","duplicate source",lambda:platform.submit("SubmitGeneration",json.dumps(duplicate)))
        overlapping=dict(duplicate,generationRecordId=new_id("generation"),sourceRef=sha256(new_id("source").encode()))
        rejected("chaincode rejects overlap when API is bypassed","overlapping",lambda:platform.submit("SubmitGeneration",json.dumps(overlapping)))
        rejected("VerifierMSP cannot submit generation records","permission denied",lambda:verifier.submit("SubmitGeneration",json.dumps(overlapping)))
        p1=packaged(first["generationRecordId"])
        manifest=company.storage.get(p1["evidenceHash"])
        assert json.loads(manifest)["records"][0]["evidenceHash"]==first["evidenceHash"]
        passed("company evidence package hash matches preserved private manifest")
        reason_hash=sha256(b"request correction")
        signature=verifier.sign(decision_payload(p1["packageId"],p1["evidenceHash"],"corrections_requested",reason_hash))
        rejected("platform cannot sign a decision even with a verifier-generated signature","permission denied",lambda:platform.submit("RecordDecision",p1["packageId"],"corrections_requested",reason_hash,signature))
        if p1["status"]=="submitted":
            rejected("tampered decision outcome invalidates detached signature","invalid verifier signature",lambda:verifier.submit("RecordDecision",p1["packageId"],"approved",reason_hash,signature))
        requested=decide(p1,"corrections_requested","Mock verifier: provide corrected calibration reading.")
        assert verify_decision(requested)
        passed("verifier signed a corrections-requested decision")
        second=imported(correction,first["generationRecordId"])
        assert second["version"]==2 and second["previousGenerationRecordId"]==first["generationRecordId"]
        old=platform.query("GetGenerationRecord",first["generationRecordId"])
        assert old["status"]=="superseded" and old["supersededBy"]==second["generationRecordId"]
        assert company.storage.get(first["evidenceHash"])==original
        assert company.storage.get(second["evidenceHash"])==correction
        passed("correction creates linked version 2 and preserves both exact raw responses")
        p2=packaged(second["generationRecordId"],p1["packageId"])
        approved=decide(p2,"approved","Mock verifier: corrected solar evidence checked and accepted.")
        assert verify_decision(approved)
        passed("verifier signed approval of revised evidence package")
        # Exercise rejection on a separate, adjacent accepted interval.
        rejected_raw=json.loads(original);rejected_raw.update(sourceId="mock-rohan-20260920-02",measurementStart="2026-09-20T07:00:00Z",measurementEnd="2026-09-20T08:00:00Z",receivedTimestamp="2026-09-20T08:05:00Z")
        third=imported(canonical(rejected_raw));p3=packaged(third["generationRecordId"])
        negative=decide(p3,"rejected","Mock verifier: supporting calibration evidence is insufficient.")
        assert verify_decision(negative)
        passed("verifier signed rejection of separate evidence package")
        for pkg in (p1,p2,p3):
            a=platform.query("GetDecision",pkg["packageId"]);b=verifier.query("GetDecision",pkg["packageId"])
            assert a==b and verify_decision(a)
        passed("both MSP queries return identical independently verified signed decisions")
        public=[platform.query("GetGenerationRecord",r["generationRecordId"]) for r in (first,second,third)]+[requested,approved,negative,p1,p2,p3]
        serialized=json.dumps(public)
        for forbidden in ("Rohan","MOCK-ROHAN","0.125","0.120","receivedTimestamp","farmerName"):
            assert forbidden not in serialized
        passed("ledger DTOs contain no farmer details, external meter/source IDs, raw readings or MWh")
        rejected("approved package decision is immutable","already exists",lambda:verifier.submit("RecordDecision",p2["packageId"],"rejected",reason_hash,signature))
        locked=dict(second,generationRecordId=new_id("generation"),sourceRef=sha256(new_id("source").encode()),previousGenerationRecordId=second["generationRecordId"],version=3,status="accepted",supersededBy="",packageId="")
        rejected("approved generation evidence cannot be silently corrected","locked",lambda:platform.submit("SubmitGeneration",json.dumps(locked)))
        report={"channel":"carbonate","chaincodeVersion":"0.2.0","checks":checks,"generationRecordIds":[r["generationRecordId"] for r in (first,second,third)],"packageIds":[p["packageId"] for p in (p1,p2,p3)],"decisions":[requested,approved,negative]}
        destination=ROOT/"docs/workflow-evidence.json"
        destination.write_text(json.dumps(report,indent=2)+"\n")
        print(f"PASS: {len(checks)} live checks; public signed evidence saved to {destination}",flush=True)
    finally:
        company.close();reviewer.close()

if __name__=="__main__":main()
