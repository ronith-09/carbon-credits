// SPDX-License-Identifier: Apache-2.0
package main

import (
	"errors"
	"github.com/hyperledger/fabric-chaincode-go/v2/pkg/cid"
	"github.com/hyperledger/fabric-chaincode-go/v2/shim"
	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
	"testing"
)

type identity struct {
	cid.ClientIdentity
	msp string
	err error
}

func (i identity) GetMSPID() (string, error) { return i.msp, i.err }

// Any unexpected state access or write panics through the embedded nil interface.
type readOnlyStub struct{ shim.ChaincodeStubInterface }

func (readOnlyStub) GetChannelID() string { return "carbonate" }
func TestPilotMembershipAndReadOnlyQuery(t *testing.T) {
	for _, msp := range []string{"CarbonCreditPlatformMSP", "VerifierMSP", "UnknownMSP"} {
		t.Run(msp, func(t *testing.T) {
			ctx := new(contractapi.TransactionContext)
			ctx.SetStub(readOnlyStub{})
			ctx.SetClientIdentity(identity{msp: msp})
			info, err := new(PilotContract).GetPilotInfo(ctx)
			if msp == "UnknownMSP" {
				if err == nil || info != nil {
					t.Fatal("non-member accepted")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if info.Name != "carbonate" || info.Version != "0.2.0" || info.Channel != "carbonate" || info.ClientMSP != msp {
				t.Fatalf("unexpected info: %+v", info)
			}
		})
	}
}
func TestIdentityFailure(t *testing.T) {
	ctx := new(contractapi.TransactionContext)
	want := errors.New("unavailable identity")
	ctx.SetClientIdentity(identity{err: want})
	if _, err := new(PilotContract).GetPilotInfo(ctx); !errors.Is(err, want) {
		t.Fatalf("got %v", err)
	}
}
func TestContractMetadata(t *testing.T) {
	if _, err := contractapi.NewChaincode(&PilotContract{}); err != nil {
		t.Fatal(err)
	}
}
