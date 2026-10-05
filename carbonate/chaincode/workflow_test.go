// SPDX-License-Identifier: Apache-2.0
package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/json"
	"math/big"
	"strings"
	"testing"
	"time"

	"github.com/hyperledger/fabric-chaincode-go/v2/pkg/cid"
	"github.com/hyperledger/fabric-chaincode-go/v2/shim"
	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
	"google.golang.org/protobuf/types/known/timestamppb"
)

type workflowIdentity struct {
	cid.ClientIdentity
	msp  string
	cert *x509.Certificate
}

func (i workflowIdentity) GetMSPID() (string, error)                      { return i.msp, nil }
func (i workflowIdentity) GetID() (string, error)                         { return "x509-test-" + i.msp, nil }
func (i workflowIdentity) GetX509Certificate() (*x509.Certificate, error) { return i.cert, nil }

type stateStub struct {
	shim.ChaincodeStubInterface
	state map[string][]byte
	reads map[string]bool
}

func (s *stateStub) GetState(key string) ([]byte, error) {
	s.reads[key] = true
	return s.state[key], nil
}
func (s *stateStub) PutState(key string, data []byte) error {
	s.state[key] = append([]byte{}, data...)
	return nil
}
func (s *stateStub) GetChannelID() string { return "carbonate" }
func (s *stateStub) GetTxID() string      { return "test-transaction" }
func (s *stateStub) GetTxTimestamp() (*timestamppb.Timestamp, error) {
	return timestamppb.New(time.Date(2026, 9, 25, 0, 0, 0, 0, time.UTC)), nil
}

type workflowTest struct {
	c    *PilotContract
	ctx  *contractapi.TransactionContext
	stub *stateStub
	key  *ecdsa.PrivateKey
	cert *x509.Certificate
}

func harness(t *testing.T) *workflowTest {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "verifier-test"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour)}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	stub := &stateStub{state: map[string][]byte{}, reads: map[string]bool{}}
	ctx := new(contractapi.TransactionContext)
	ctx.SetStub(stub)
	w := &workflowTest{&PilotContract{}, ctx, stub, key, cert}
	w.org(platformMSP)
	return w
}
func (w *workflowTest) org(msp string) {
	w.ctx.SetClientIdentity(workflowIdentity{msp: msp, cert: w.cert})
}
func hash(text string) string { return fmtHash(sha256.Sum256([]byte(text))) }
func fmtHash(h [32]byte) string {
	const hex = "0123456789abcdef"
	s := make([]byte, 64)
	for i, b := range h {
		s[i*2] = hex[b>>4]
		s[i*2+1] = hex[b&15]
	}
	return string(s)
}
func sample(id, start, end string) Generation {
	return Generation{ID: id, MeterRef: "meter-opaque", Start: "2026-09-20T" + start + ":00Z", End: "2026-09-20T" + end + ":00Z", EvidenceHash: hash("raw-" + id), SourceRef: hash("source-" + id), Version: 1, Status: "accepted"}
}
func (w *workflowTest) submit(r Generation) error {
	b, _ := json.Marshal(r)
	return w.c.SubmitGeneration(w.ctx, string(b))
}
func (w *workflowTest) sign(pkg, outcome, reason string) string {
	p, _ := w.c.GetEvidencePackage(w.ctx, pkg)
	sum := sha256.Sum256([]byte(decisionPayload("carbonate", pkg, p.Hash, outcome, reason)))
	sig, _ := ecdsa.SignASN1(rand.Reader, w.key, sum[:])
	return base64.StdEncoding.EncodeToString(sig)
}
func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}
func fails(t *testing.T, err error, fragment string) {
	t.Helper()
	if err == nil || !strings.Contains(err.Error(), fragment) {
		t.Fatalf("wanted %q, got %v", fragment, err)
	}
}

func TestDuplicateOverlapAndAdjacentPeriods(t *testing.T) {
	w := harness(t)
	r := sample("g1", "06:00", "07:00")
	must(t, w.submit(r))
	duplicate := sample("g2", "08:00", "09:00")
	duplicate.SourceRef = r.SourceRef
	fails(t, w.submit(duplicate), "duplicate source")
	for _, pair := range [][2]string{{"06:30", "07:30"}, {"05:00", "08:00"}, {"06:15", "06:45"}, {"06:00", "07:00"}} {
		fails(t, w.submit(sample("overlap", pair[0], pair[1])), "overlapping")
	}
	must(t, w.submit(sample("adjacent", "07:00", "08:00")))
	if !w.stub.reads["meter/meter-opaque"] || !w.stub.reads["source/"+r.SourceRef] {
		t.Fatal("missing concurrency guard reads")
	}
}
func TestCorrectionsAreLinkedAndOriginalEvidenceImmutable(t *testing.T) {
	w := harness(t)
	first := sample("g1", "06:00", "07:00")
	must(t, w.submit(first))
	before := string(w.stub.state["generation/g1"])
	correction := sample("g2", "06:00", "07:00")
	correction.PreviousID = "g1"
	correction.Version = 2
	must(t, w.submit(correction))
	if before != string(w.stub.state["generation/g1"]) {
		t.Fatal("original evidence overwritten")
	}
	previous, err := w.c.GetGenerationRecord(w.ctx, "g1")
	must(t, err)
	if previous.Status != "superseded" || previous.SupersededBy != "g2" {
		t.Fatal(previous)
	}
	correction.ID = "g3"
	correction.SourceRef = hash("third")
	fails(t, w.submit(correction), "current version")
	correction.PreviousID = "g2"
	correction.Version = 4
	fails(t, w.submit(correction), "current version")
	correction.Version = 3
	correction.MeterRef = "another-meter"
	fails(t, w.submit(correction), "same meter")
}
func TestMSPPermissionsAndNoPersonalFields(t *testing.T) {
	w := harness(t)
	r := sample("g1", "06:00", "07:00")
	for _, org := range []string{verifierMSP, "OutsiderMSP"} {
		w.org(org)
		fails(t, w.submit(r), "permission denied")
		fails(t, w.c.SubmitEvidencePackage(w.ctx, "p", "[]", hash("p"), ""), "permission denied")
	}
	w.org(platformMSP)
	fails(t, w.c.RecordDecision(w.ctx, "p", "approved", hash("reason"), ""), "permission denied")
	b, _ := json.Marshal(r)
	pii := strings.TrimSuffix(string(b), "}") + `,"farmerName":"Rohan"}`
	fails(t, w.c.SubmitGeneration(w.ctx, pii), "unknown field")
	w.org("OutsiderMSP")
	_, err := w.c.GetGenerationRecord(w.ctx, "g1")
	fails(t, err, "permission denied")
}
func TestSignedCorrectionRequestThenApprovalVisibleToBothMSPs(t *testing.T) {
	w := harness(t)
	must(t, w.submit(sample("g1", "06:00", "07:00")))
	must(t, w.c.SubmitEvidencePackage(w.ctx, "p1", `["g1"]`, hash("manifest1"), ""))
	correction := sample("g2", "06:00", "07:00")
	correction.PreviousID = "g1"
	correction.Version = 2
	fails(t, w.submit(correction), "locked")
	w.org(verifierMSP)
	reason := hash("calibration evidence required")
	sig := w.sign("p1", "corrections_requested", reason)
	fails(t, w.c.RecordDecision(w.ctx, "p1", "approved", reason, sig), "invalid verifier signature")
	must(t, w.c.RecordDecision(w.ctx, "p1", "corrections_requested", reason, sig))
	fails(t, w.c.RecordDecision(w.ctx, "p1", "rejected", reason, sig), "already exists")
	w.org(platformMSP)
	must(t, w.submit(correction))
	fails(t, w.c.SubmitEvidencePackage(w.ctx, "old", `["g1"]`, hash("old"), "p1"), "not active")
	must(t, w.c.SubmitEvidencePackage(w.ctx, "p2", `["g2"]`, hash("manifest2"), "p1"))
	w.org(verifierMSP)
	sig = w.sign("p2", "approved", reason)
	must(t, w.c.RecordDecision(w.ctx, "p2", "approved", reason, sig))
	var original string
	for _, org := range []string{platformMSP, verifierMSP} {
		w.org(org)
		d, err := w.c.GetDecision(w.ctx, "p2")
		must(t, err)
		encoded, _ := json.Marshal(d)
		if original != "" && original != string(encoded) {
			t.Fatal("org queries differ")
		}
		original = string(encoded)
		sum := sha256.Sum256([]byte(d.Payload))
		raw, _ := base64.StdEncoding.DecodeString(d.Signature)
		if !ecdsa.VerifyASN1(&w.key.PublicKey, sum[:], raw) {
			t.Fatal("signature invalid")
		}
		if d.SignerMSP != verifierMSP || d.Outcome != "approved" {
			t.Fatal(d)
		}
	}
	w.org(platformMSP)
	correction.ID = "g3"
	correction.SourceRef = hash("third")
	correction.PreviousID = "g2"
	correction.Version = 3
	fails(t, w.submit(correction), "locked")
}
func TestRejectAndPackageMembershipValidation(t *testing.T) {
	w := harness(t)
	must(t, w.submit(sample("g1", "06:00", "07:00")))
	fails(t, w.c.SubmitEvidencePackage(w.ctx, "p", `["g1","g1"]`, hash("p"), ""), "duplicate package")
	fails(t, w.c.SubmitEvidencePackage(w.ctx, "p", `[]`, hash("p"), ""), "1 to 1000")
	must(t, w.c.SubmitEvidencePackage(w.ctx, "p", `["g1"]`, hash("p"), ""))
	fails(t, w.c.SubmitEvidencePackage(w.ctx, "other", `["g1"]`, hash("p"), ""), "another package")
	w.org(verifierMSP)
	reason := hash("evidence rejected")
	must(t, w.c.RecordDecision(w.ctx, "p", "rejected", reason, w.sign("p", "rejected", reason)))
	p, err := w.c.GetEvidencePackage(w.ctx, "p")
	must(t, err)
	if p.Status != "rejected" {
		t.Fatal(p)
	}
}
