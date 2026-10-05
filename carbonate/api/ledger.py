"""Pilot adapter to the existing Drunix CLI; no HTTP endpoint exposes admin keys."""
import base64
import json
import os
import subprocess
from pathlib import Path
from .storage import ROOT

class LedgerError(RuntimeError):
    pass

class FabricLedger:
    def __init__(self, org="platform"):
        if org not in ("platform", "verifier"):
            raise ValueError("unknown organisation")
        self.org = org
        self.network = ROOT / "network"
        self.domain = f"{'platform' if org == 'platform' else 'verifier'}.carbonate.local"
        self.msp = "CarbonCreditPlatformMSP" if org == "platform" else "VerifierMSP"
        self.identity = self.network / f"organizations/peerOrganizations/{self.domain}/users/Admin@{self.domain}/msp"

    def environment(self):
        env = os.environ.copy()
        env.update(FABRIC_CFG_PATH=str(self.network), CORE_PEER_TLS_ENABLED="true", CORE_PEER_LOCALMSPID=self.msp,
                   CORE_PEER_ADDRESS="localhost:7051" if self.org == "platform" else "localhost:9051",
                   CORE_PEER_TLS_ROOTCERT_FILE=str(self.network / f"organizations/peerOrganizations/{self.domain}/tlsca/tlsca.{self.domain}-cert.pem"),
                   CORE_PEER_MSPCONFIGPATH=str(self.identity))
        return env

    def _run(self, args):
        executable = ROOT / ".tools/bin/peer"
        process = subprocess.run([str(executable) if executable.exists() else "peer", *args], env=self.environment(), capture_output=True, text=True, timeout=150)
        if process.returncode:
            raise LedgerError((process.stderr + process.stdout).strip())
        return process.stdout

    def query(self, method, *args):
        result = self._run(["chaincode", "query", "-C", "carbonate", "-n", "carbonate", "-c", json.dumps({"Args":[method,*args]})])
        return json.loads(result)

    def find(self, method, *args):
        try:
            return self.query(method, *args)
        except LedgerError as error:
            if "not found:" in str(error):
                return None
            raise

    def submit(self, method, *args):
        orderer_ca = self.network / "organizations/ordererOrganizations/carbonate.local/tlsca/tlsca.carbonate.local-cert.pem"
        command = ["chaincode", "invoke", "-o", "localhost:7050", "--ordererTLSHostnameOverride", "orderer.carbonate.local", "--tls", "--cafile", str(orderer_ca),
                   "-C", "carbonate", "-n", "carbonate", "--waitForEvent", "--waitForEventTimeout", "120s", "-c", json.dumps({"Args":[method,*args]})]
        for domain, port in [("platform.carbonate.local",7051),("verifier.carbonate.local",9051)]:
            command.extend(["--peerAddresses",f"localhost:{port}","--tlsRootCertFiles",str(self.network / f"organizations/peerOrganizations/{domain}/tlsca/tlsca.{domain}-cert.pem")])
        self._run(command)

    def sign(self, payload):
        keys = list((self.identity / "keystore").glob("*"))
        if len(keys) != 1:
            raise LedgerError("expected one signing key in selected MSP identity")
        result = subprocess.run(["openssl","dgst","-sha256","-sign",str(keys[0])], input=payload.encode(), capture_output=True, check=True)
        return base64.b64encode(result.stdout).decode()


def verify_decision(decision):
    """Independently verify the published signature (MSP trust is enforced on-ledger)."""
    import tempfile
    expected = decision_payload(decision["packageId"], decision["packageHash"], decision["outcome"], decision["reasonHash"])
    if decision["signerMSP"] != "VerifierMSP" or decision["signedPayload"] != expected:
        raise LedgerError("decision identity or payload mismatch")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root/"cert.pem").write_text(decision["signerCertificate"])
        (root/"payload").write_text(expected)
        (root/"signature").write_bytes(base64.b64decode(decision["signature"], validate=True))
        public = subprocess.run(["openssl","x509","-in",str(root/"cert.pem"),"-pubkey","-noout"],capture_output=True,check=True).stdout
        (root/"public.pem").write_bytes(public)
        subprocess.run(["openssl","dgst","-sha256","-verify",str(root/"public.pem"),"-signature",str(root/"signature"),str(root/"payload")],capture_output=True,check=True)
    return True


def decision_payload(package_id, package_hash, outcome, reason_hash):
    return "\n".join(["CarbonateVerification/v1","carbonate",package_id,package_hash,outcome,reason_hash])
