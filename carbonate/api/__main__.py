"""Run from the repository root: python3 -m carbonate.api --help."""
import argparse
import json
from pathlib import Path
from .service import CarbonateService
from .ledger import FabricLedger, verify_decision
from ..connectors.mock_solar import MockSolarAPI


def main():
    parser = argparse.ArgumentParser(description="Carbonate local pilot operator interface")
    parser.add_argument("--org",choices=["platform","verifier"],default="platform")
    parser.add_argument("--private-root",type=Path)
    commands = parser.add_subparsers(dest="command",required=True)
    commands.add_parser("seed-rohan")
    register = commands.add_parser("register")
    register.add_argument("--name",required=True); register.add_argument("--source-meter-id",required=True)
    reading = commands.add_parser("import")
    reading.add_argument("response",type=Path); reading.add_argument("--corrects")
    package = commands.add_parser("package")
    package.add_argument("records",nargs="+"); package.add_argument("--previous-package")
    decision = commands.add_parser("decide")
    decision.add_argument("package_id"); decision.add_argument("outcome",choices=["approved","rejected","corrections_requested"])
    decision.add_argument("--reason-file",type=Path,required=True)
    query = commands.add_parser("query")
    query.add_argument("kind",choices=["generation","package","decision"]); query.add_argument("id")
    commands.add_parser("sync")
    args = parser.parse_args()
    service = CarbonateService(FabricLedger(args.org),args.private_root)
    try:
        if args.command=="seed-rohan": result=service.seed_rohan()
        elif args.command=="register": result=service.register(args.name,args.source_meter_id)
        elif args.command=="import": result=service.import_reading(MockSolarAPI().fetch(args.response),args.corrects)
        elif args.command=="package": result=service.package(args.records,args.previous_package)
        elif args.command=="decide": result=service.decide(args.package_id,args.outcome,args.reason_file.read_text())
        elif args.command=="sync": service.sync(); result={"status":"synchronized"}
        else:
            result=service.ledger.query({"generation":"GetGenerationRecord","package":"GetEvidencePackage","decision":"GetDecision"}[args.kind],args.id)
            if args.kind=="decision": verify_decision(result)
        print(json.dumps(result,indent=2))
    finally:
        service.close()

if __name__ == "__main__":
    main()
