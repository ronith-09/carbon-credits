import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from carbonate.api.service import CarbonateService, ValidationError
from carbonate.api.ledger import LedgerError
from carbonate.api.storage import ROOT, sha256

class FakeLedger:
    def __init__(self,org="platform",state=None):
        self.org=org
        self.state=state if state is not None else {"generation":{},"package":{},"decision":{}}
        self.fail_before=False; self.lose_ack=False; self.submissions=0
    def find(self,method,id):
        kind={"GetGenerationRecord":"generation","GetEvidencePackage":"package","GetDecision":"decision"}[method]
        return copy.deepcopy(self.state[kind].get(id))
    def query(self,method,id):
        result=self.find(method,id)
        if result is None: raise LedgerError("not found: "+id)
        return result
    def sign(self,payload): return "mock-signature-"+sha256(payload.encode())
    def submit(self,method,*args):
        if self.fail_before: raise LedgerError("mock outage")
        self.submissions+=1
        if method=="SubmitGeneration":
            r=json.loads(args[0]); self.state["generation"][r["generationRecordId"]]=r
            if r["previousGenerationRecordId"]:
                old=self.state["generation"][r["previousGenerationRecordId"]]; old["status"]="superseded";old["supersededBy"]=r["generationRecordId"]
        elif method=="SubmitEvidencePackage":
            id,ids,hash,previous=args
            self.state["package"][id]=dict(packageId=id,generationRecordIds=json.loads(ids),evidenceHash=hash,previousPackageId=previous,status="submitted")
            for record in json.loads(ids):self.state["generation"][record]["packageId"]=id
        else:
            id,outcome,reason,sig=args
            self.state["decision"][id]=dict(packageId=id,outcome=outcome,reasonHash=reason,signature=sig,signerMSP="VerifierMSP")
            self.state["package"][id]["status"]=outcome
        if self.lose_ack:
            self.lose_ack=False; raise LedgerError("committed, acknowledgement lost")

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.ledger=FakeLedger();self.app=CarbonateService(self.ledger,self.tmp.name);self.person=self.app.seed_rohan()
        self.raw=(ROOT/"connectors/fixtures/rohan-reading.json").read_bytes()
        self.correction=(ROOT/"connectors/fixtures/rohan-correction.json").read_bytes()
    def tearDown(self):self.app.close();self.tmp.cleanup()
    def modified(self,**fields):
        r=json.loads(self.raw);r.update(fields);return json.dumps(r).encode()
    def test_registration_and_privacy(self):
        self.assertEqual(self.app.seed_rohan(),self.person)
        self.assertEqual(self.app.db.execute("SELECT name FROM farmers").fetchone()[0],"Rohan")
        record=self.app.import_reading(self.raw)
        for forbidden in ("Rohan","MOCK-ROHAN","0.125","farmerName","receivedTimestamp"):
            self.assertNotIn(forbidden,json.dumps(self.ledger.state))
        self.assertEqual(self.app.storage.get(record["evidenceHash"]),self.raw)
        self.assertEqual((Path(self.tmp.name)/"application.sqlite3").stat().st_mode & 0o777,0o600)
        self.assertEqual(self.app.storage.objects.stat().st_mode & 0o777,0o700)
    def test_duplicate_and_overlapping_readings(self):
        self.app.import_reading(self.raw)
        with self.assertRaisesRegex(ValidationError,"duplicate source"):self.app.import_reading(self.raw)
        with self.assertRaisesRegex(ValidationError,"overlapping"):self.app.import_reading(self.modified(sourceId="other",measurementStart="2026-09-20T06:30:00Z",measurementEnd="2026-09-20T07:30:00Z",receivedTimestamp="2026-09-20T08:00:00Z"))
        adjacent=self.app.import_reading(self.modified(sourceId="adjacent",measurementStart="2026-09-20T07:00:00Z",measurementEnd="2026-09-20T08:00:00Z",receivedTimestamp="2026-09-20T08:05:00Z"))
        self.assertEqual(adjacent["version"],1)
        self.assertEqual(self.app.db.execute("SELECT count(*) FROM intakes WHERE outcome='rejected'").fetchone()[0],2)
    def test_validation_of_units_dates_and_meter(self):
        for fields in ({"MWh":"NaN"},{"MWh":"-1"},{"MWh":"0"},{"meterId":"unknown"},{"measurementEnd":"2026-09-20T05:00:00Z"},{"measurementStart":"2026-09-20T06:00:00"}):
            with self.subTest(fields=fields),self.assertRaises(ValidationError):self.app.import_reading(self.modified(**fields))
        self.assertEqual(self.app.db.execute("SELECT count(*) FROM generation_records").fetchone()[0],0)
    def test_rohan_correction_to_verifier_flow(self):
        first=self.app.import_reading(self.raw);p1=self.app.package([first["generationRecordId"]])
        with self.assertRaisesRegex(ValidationError,"locked"):self.app.import_reading(self.correction,first["generationRecordId"])
        verifier=CarbonateService(FakeLedger("verifier",self.ledger.state),self.tmp.name)
        try:
            d=verifier.decide(p1["packageId"],"corrections_requested","Check calibration")
            self.assertEqual(d["signerMSP"],"VerifierMSP")
            second=self.app.import_reading(self.correction,first["generationRecordId"])
            self.assertEqual(second["version"],2)
            self.assertEqual(second["previousGenerationRecordId"],first["generationRecordId"])
            self.assertEqual(self.app.storage.get(first["evidenceHash"]),self.raw)
            self.assertEqual(self.app.storage.get(second["evidenceHash"]),self.correction)
            with self.assertRaisesRegex(sqlite3.IntegrityError,"immutable"):
                self.app.db.execute("UPDATE generation_records SET mwh='999'")
            with self.assertRaisesRegex(sqlite3.IntegrityError,"immutable"):
                self.app.db.execute("DELETE FROM generation_records")
            p2=self.app.package([second["generationRecordId"]],p1["packageId"])
            signed=verifier.decide(p2["packageId"],"approved","Evidence checked")
            self.assertEqual(self.ledger.query("GetDecision",p2["packageId"]),signed)
            self.assertEqual(verifier.ledger.query("GetDecision",p2["packageId"]),signed)
            with self.assertRaisesRegex(ValidationError,"already has a decision"):verifier.decide(p2["packageId"],"rejected","changed mind")
        finally:verifier.close()
    def test_msp_permissions(self):
        with self.assertRaises(PermissionError):self.app.decide("p","approved","company cannot sign")
        verifier=CarbonateService(FakeLedger("verifier",self.ledger.state),self.tmp.name)
        try:
            with self.assertRaises(PermissionError):verifier.seed_rohan()
            with self.assertRaises(PermissionError):verifier.import_reading(self.raw)
            with self.assertRaises(PermissionError):verifier.package(["record"])
        finally:verifier.close()
    def test_outbox_recovery_after_outage_and_lost_ack(self):
        self.ledger.fail_before=True
        with self.assertRaisesRegex(LedgerError,"publication pending"):self.app.import_reading(self.raw)
        stored=self.app.db.execute("SELECT id FROM generation_records").fetchone()[0]
        with self.assertRaisesRegex(ValidationError,"duplicate source"):self.app.import_reading(self.raw)
        self.ledger.fail_before=False;self.ledger.lose_ack=True
        with self.assertRaises(LedgerError):self.app.sync()
        self.app.close();self.app=CarbonateService(self.ledger,self.tmp.name);self.app.sync()
        self.assertEqual(self.ledger.submissions,1)
        self.assertEqual(self.app.db.execute("SELECT state FROM outbox WHERE entity_id=?",(stored,)).fetchone()[0],"done")
    def test_concurrent_duplicate_imports(self):
        barrier=threading.Barrier(2);results=[]
        def work():
            app=CarbonateService(self.ledger,self.tmp.name)
            try:
                barrier.wait();app.import_reading(self.raw);results.append("accepted")
            except ValidationError:results.append("duplicate")
            finally:app.close()
        threads=[threading.Thread(target=work) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=40)
        self.assertCountEqual(results,["accepted","duplicate"])
        self.assertEqual(self.app.db.execute("SELECT count(*) FROM generation_records").fetchone()[0],1)

if __name__=="__main__":unittest.main()
