// SPDX-License-Identifier: Apache-2.0
package main

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"regexp"
	"sort"
	"time"

	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
)

const platformMSP = "CarbonCreditPlatformMSP"
const verifierMSP = "VerifierMSP"

var opaqueID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`)
var digest = regexp.MustCompile(`^[a-f0-9]{64}$`)

// Generation evidence is immutable. Status/successor are projections at query time.
// SourceRef is a SHA-256 reference, never a raw external identifier or farmer data.
type Generation struct {
	ID           string `json:"generationRecordId"`
	MeterRef     string `json:"meterRef"`
	Start        string `json:"measurementStart"`
	End          string `json:"measurementEnd"`
	EvidenceHash string `json:"evidenceHash"`
	SourceRef    string `json:"sourceRef"`
	Version      int    `json:"version"`
	PreviousID   string `json:"previousGenerationRecordId"`
	Status       string `json:"status"`
	SupersededBy string `json:"supersededBy"`
	PackageID    string `json:"packageId"`
}
type EvidencePackage struct {
	ID         string   `json:"packageId"`
	RecordIDs  []string `json:"generationRecordIds"`
	Hash       string   `json:"evidenceHash"`
	PreviousID string   `json:"previousPackageId"`
	Status     string   `json:"status"`
}
type Decision struct {
	PackageID     string `json:"packageId"`
	PackageHash   string `json:"packageHash"`
	Outcome       string `json:"outcome"`
	ReasonHash    string `json:"reasonHash"`
	SignerMSP     string `json:"signerMSP"`
	SignerID      string `json:"signerId"`
	Certificate   string `json:"signerCertificate"`
	Payload       string `json:"signedPayload"`
	Signature     string `json:"signature"`
	TransactionID string `json:"transactionId"`
	Timestamp     string `json:"timestamp"`
}

func authorize(ctx contractapi.TransactionContextInterface, allowed ...string) error {
	msp, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("read client MSP: %w", err)
	}
	for _, a := range allowed {
		if msp == a {
			return nil
		}
	}
	return fmt.Errorf("permission denied for MSP %s", msp)
}
func strictJSON(text string, target interface{}) error {
	d := json.NewDecoder(bytes.NewBufferString(text))
	d.DisallowUnknownFields()
	if err := d.Decode(target); err != nil {
		return err
	}
	if err := d.Decode(new(interface{})); err != io.EOF {
		return fmt.Errorf("expected one JSON value")
	}
	return nil
}
func read(ctx contractapi.TransactionContextInterface, key string, value interface{}) error {
	data, err := ctx.GetStub().GetState(key)
	if err != nil {
		return err
	}
	if data == nil {
		return fmt.Errorf("not found: %s", key)
	}
	return json.Unmarshal(data, value)
}
func put(ctx contractapi.TransactionContextInterface, key string, value interface{}) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	return ctx.GetStub().PutState(key, data)
}
func vacant(ctx contractapi.TransactionContextInterface, key string) error {
	data, err := ctx.GetStub().GetState(key)
	if err != nil {
		return err
	}
	if data != nil {
		return fmt.Errorf("already exists: %s", key)
	}
	return nil
}
func period(start, end string) error {
	a, err := time.Parse("2006-01-02T15:04:05Z", start)
	if err != nil {
		return fmt.Errorf("invalid start: %w", err)
	}
	b, err := time.Parse("2006-01-02T15:04:05Z", end)
	if err != nil {
		return fmt.Errorf("invalid end: %w", err)
	}
	if !a.Before(b) {
		return fmt.Errorf("measurement start must precede end")
	}
	return nil
}
func (p *PilotContract) GetGenerationRecord(ctx contractapi.TransactionContextInterface, id string) (*Generation, error) {
	if err := authorize(ctx, platformMSP, verifierMSP); err != nil {
		return nil, err
	}
	r := new(Generation)
	if err := read(ctx, "generation/"+id, r); err != nil {
		return nil, err
	}
	successor, err := ctx.GetStub().GetState("successor/" + id)
	if err != nil {
		return nil, err
	}
	if successor != nil {
		r.Status = "superseded"
		r.SupersededBy = string(successor)
	}
	lock, err := ctx.GetStub().GetState("recordPackage/" + id)
	if err != nil {
		return nil, err
	}
	r.PackageID = string(lock)
	return r, nil
}
func (p *PilotContract) GetEvidencePackage(ctx contractapi.TransactionContextInterface, id string) (*EvidencePackage, error) {
	if err := authorize(ctx, platformMSP, verifierMSP); err != nil {
		return nil, err
	}
	result := new(EvidencePackage)
	if err := read(ctx, "package/"+id, result); err != nil {
		return nil, err
	}
	data, err := ctx.GetStub().GetState("decision/" + id)
	if err != nil {
		return nil, err
	}
	if data != nil {
		var d Decision
		if err := json.Unmarshal(data, &d); err != nil {
			return nil, err
		}
		result.Status = d.Outcome
	}
	return result, nil
}
func (p *PilotContract) GetDecision(ctx contractapi.TransactionContextInterface, packageID string) (*Decision, error) {
	if err := authorize(ctx, platformMSP, verifierMSP); err != nil {
		return nil, err
	}
	d := new(Decision)
	if err := read(ctx, "decision/"+packageID, d); err != nil {
		return nil, err
	}
	return d, nil
}

// SubmitGeneration serializes changes through one active-index key per meter.
// Reading and writing that key puts concurrent overlap/correction races in the
// transaction read/write set. Source and record keys also guard duplicate races.
func (p *PilotContract) SubmitGeneration(ctx contractapi.TransactionContextInterface, recordJSON string) error {
	if err := authorize(ctx, platformMSP); err != nil {
		return err
	}
	var r Generation
	if err := strictJSON(recordJSON, &r); err != nil {
		return err
	}
	if !opaqueID.MatchString(r.ID) || !opaqueID.MatchString(r.MeterRef) || !digest.MatchString(r.EvidenceHash) || !digest.MatchString(r.SourceRef) {
		return fmt.Errorf("invalid record ID, meter reference or digest")
	}
	if r.Status != "accepted" || r.SupersededBy != "" || r.PackageID != "" {
		return fmt.Errorf("new evidence must have accepted status and no successor")
	}
	if err := period(r.Start, r.End); err != nil {
		return err
	}
	if err := vacant(ctx, "generation/"+r.ID); err != nil {
		return err
	}
	if err := vacant(ctx, "source/"+r.SourceRef); err != nil {
		return fmt.Errorf("duplicate source reference: %w", err)
	}
	active := []string{}
	data, err := ctx.GetStub().GetState("meter/" + r.MeterRef)
	if err != nil {
		return err
	}
	if data != nil {
		if err := json.Unmarshal(data, &active); err != nil {
			return err
		}
	}
	if r.PreviousID == "" {
		if r.Version != 1 {
			return fmt.Errorf("initial generation version must be 1")
		}
	} else {
		old, err := p.GetGenerationRecord(ctx, r.PreviousID)
		if err != nil {
			return err
		}
		if old.MeterRef != r.MeterRef || old.Status != "accepted" || r.Version != old.Version+1 {
			return fmt.Errorf("correction must link the current version of the same meter")
		}
		lock, err := ctx.GetStub().GetState("recordPackage/" + old.ID)
		if err != nil {
			return err
		}
		if lock != nil {
			pkg, err := p.GetEvidencePackage(ctx, string(lock))
			if err != nil {
				return err
			}
			if pkg.Status != "corrections_requested" && pkg.Status != "rejected" {
				return fmt.Errorf("packaged evidence is locked until corrections are requested or rejected")
			}
		}
	}
	next := []string{}
	foundPrevious := r.PreviousID == ""
	for _, id := range active {
		if id == r.PreviousID {
			foundPrevious = true
			continue
		}
		var old Generation
		if err := read(ctx, "generation/"+id, &old); err != nil {
			return err
		}
		// Canonical UTC timestamps sort chronologically. Periods are half-open [start,end).
		if r.Start < old.End && r.End > old.Start {
			return fmt.Errorf("overlapping active period for meter %s", r.MeterRef)
		}
		next = append(next, id)
	}
	if !foundPrevious {
		return fmt.Errorf("correction predecessor is not active")
	}
	if len(next) >= 10000 {
		return fmt.Errorf("pilot meter record limit reached")
	}
	next = append(next, r.ID)
	sort.Strings(next)
	if err := put(ctx, "generation/"+r.ID, &r); err != nil {
		return err
	}
	if err := ctx.GetStub().PutState("source/"+r.SourceRef, []byte(r.ID)); err != nil {
		return err
	}
	if r.PreviousID != "" {
		if err := ctx.GetStub().PutState("successor/"+r.PreviousID, []byte(r.ID)); err != nil {
			return err
		}
	}
	return put(ctx, "meter/"+r.MeterRef, next)
}
func (p *PilotContract) SubmitEvidencePackage(ctx contractapi.TransactionContextInterface, id, recordIDsJSON, hash, previousID string) error {
	if err := authorize(ctx, platformMSP); err != nil {
		return err
	}
	if !opaqueID.MatchString(id) || !digest.MatchString(hash) {
		return fmt.Errorf("invalid package ID or hash")
	}
	if err := vacant(ctx, "package/"+id); err != nil {
		return err
	}
	var ids []string
	if err := strictJSON(recordIDsJSON, &ids); err != nil {
		return err
	}
	if len(ids) == 0 || len(ids) > 1000 {
		return fmt.Errorf("package must contain 1 to 1000 records")
	}
	if previousID != "" {
		previous, err := p.GetEvidencePackage(ctx, previousID)
		if err != nil {
			return err
		}
		if previous.Status != "corrections_requested" && previous.Status != "rejected" {
			return fmt.Errorf("previous package is not open for revision")
		}
	}
	sort.Strings(ids)
	for i, recordID := range ids {
		if i > 0 && ids[i-1] == recordID {
			return fmt.Errorf("duplicate package member")
		}
		r, err := p.GetGenerationRecord(ctx, recordID)
		if err != nil {
			return err
		}
		if r.Status != "accepted" {
			return fmt.Errorf("package member is not active")
		}
		lock, err := ctx.GetStub().GetState("recordPackage/" + recordID)
		if err != nil {
			return err
		}
		if lock != nil {
			if string(lock) != previousID {
				return fmt.Errorf("record already belongs to another package")
			}
		}
	}
	pkg := EvidencePackage{ID: id, RecordIDs: ids, Hash: hash, PreviousID: previousID, Status: "submitted"}
	if err := put(ctx, "package/"+id, &pkg); err != nil {
		return err
	}
	for _, recordID := range ids {
		if err := ctx.GetStub().PutState("recordPackage/"+recordID, []byte(id)); err != nil {
			return err
		}
	}
	return nil
}
func decisionPayload(channel, packageID, packageHash, outcome, reasonHash string) string {
	return "CarbonateVerification/v1\n" + channel + "\n" + packageID + "\n" + packageHash + "\n" + outcome + "\n" + reasonHash
}

// RecordDecision requires a verifier transaction AND a detached signature from
// that same submitting identity, binding the exact package hash and decision.
func (p *PilotContract) RecordDecision(ctx contractapi.TransactionContextInterface, packageID, outcome, reasonHash, signature string) error {
	if err := authorize(ctx, verifierMSP); err != nil {
		return err
	}
	if outcome != "approved" && outcome != "rejected" && outcome != "corrections_requested" {
		return fmt.Errorf("invalid decision outcome")
	}
	if !digest.MatchString(reasonHash) {
		return fmt.Errorf("invalid reason hash")
	}
	if err := vacant(ctx, "decision/"+packageID); err != nil {
		return err
	}
	pkg, err := p.GetEvidencePackage(ctx, packageID)
	if err != nil {
		return err
	}
	if pkg.Status != "submitted" {
		return fmt.Errorf("package is not submitted")
	}
	for _, id := range pkg.RecordIDs {
		r, err := p.GetGenerationRecord(ctx, id)
		if err != nil {
			return err
		}
		if r.Status != "accepted" {
			return fmt.Errorf("package has superseded evidence")
		}
	}
	cert, err := ctx.GetClientIdentity().GetX509Certificate()
	if err != nil {
		return err
	}
	if cert == nil {
		return fmt.Errorf("verifier certificate required")
	}
	publicKey, ok := cert.PublicKey.(*ecdsa.PublicKey)
	if !ok {
		return fmt.Errorf("pilot requires an ECDSA verifier certificate")
	}
	payload := decisionPayload(ctx.GetStub().GetChannelID(), packageID, pkg.Hash, outcome, reasonHash)
	sum := sha256.Sum256([]byte(payload))
	sig, err := base64.StdEncoding.DecodeString(signature)
	if err != nil || !ecdsa.VerifyASN1(publicKey, sum[:], sig) {
		return fmt.Errorf("invalid verifier signature")
	}
	signer, err := ctx.GetClientIdentity().GetID()
	if err != nil {
		return err
	}
	timestamp, err := ctx.GetStub().GetTxTimestamp()
	if err != nil {
		return err
	}
	d := Decision{PackageID: packageID, PackageHash: pkg.Hash, Outcome: outcome, ReasonHash: reasonHash, SignerMSP: verifierMSP, SignerID: signer, Certificate: string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert.Raw})), Payload: payload, Signature: signature, TransactionID: ctx.GetStub().GetTxID(), Timestamp: time.Unix(timestamp.Seconds, int64(timestamp.Nanos)).UTC().Format(time.RFC3339Nano)}
	return put(ctx, "decision/"+packageID, &d)
}
