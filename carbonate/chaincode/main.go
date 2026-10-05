// SPDX-License-Identifier: Apache-2.0
package main

import (
	"fmt"
	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
)

// PilotContract tracks generation evidence and verifier decisions.
// Personal details, raw readings and decision reasons stay off-ledger.
type PilotContract struct{ contractapi.Contract }

type PilotInfo struct {
	Name      string `json:"name"`
	Version   string `json:"version"`
	Channel   string `json:"channel"`
	ClientMSP string `json:"clientMSP"`
}

// GetPilotInfo verifies that the deployed chaincode executes for a pilot member.
func (p *PilotContract) GetPilotInfo(ctx contractapi.TransactionContextInterface) (*PilotInfo, error) {
	msp, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return nil, fmt.Errorf("read client MSP: %w", err)
	}
	if msp != "CarbonCreditPlatformMSP" && msp != "VerifierMSP" {
		return nil, fmt.Errorf("MSP %q is not a Carbonate pilot member", msp)
	}
	return &PilotInfo{Name: "carbonate", Version: "0.2.0", Channel: ctx.GetStub().GetChannelID(), ClientMSP: msp}, nil
}

func main() {
	cc, err := contractapi.NewChaincode(&PilotContract{})
	if err != nil {
		panic(err)
	}
	if err := cc.Start(); err != nil {
		panic(err)
	}
}
