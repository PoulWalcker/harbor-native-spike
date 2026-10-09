"""Execute every original fixture, then independently evaluate frozen native records."""

import copy
import json
import time
from pathlib import Path

from business import invoice_total

from acceptance.contracts import Rejected
from acceptance.n8n_provenance import (
    check_operation_order,
    check_rejection,
    observe_execution,
)
from native_spike.contracts import RunBinding
from native_spike.execute.n8n import execute_compiled
from native_spike.verifier import compile_case, preflight, reward, seal, write_once

ROOT = Path(__file__).parent
OUT = Path("/logs/verifier")


def main():
    try:
        config, bindings, options = preflight(ROOT, deadline_seconds=30)
        cases = json.loads((ROOT / "cases.json").read_text())
        source = (ROOT / "operations.js").read_text()
        # Compile all inputs before any execution. Invalid plans never become partial trials.
        planned = []
        for kind, values in cases.items():
            for case in values:
                cfg = copy.deepcopy(config)
                cfg["workflow"]["inputs"] = case["inputs"]
                planned.append((kind, case, cfg, compile_case(cfg, bindings, options, source)))
    except (ValueError, OSError, KeyError, TypeError) as error:
        reward(
            OUT,
            {
                "status": "rejected",
                "acceptance": False,
                "execution": None,
                "reason": type(error).__name__,
                "normalized_reward": 0,
            },
        )
        return
    evidence = OUT / "evidence"
    for kind, case, cfg, compiled in planned:
        folder = evidence / case["name"]
        record = execute_compiled(compiled, folder, RunBinding(deadline_at=time.time() + 30))
        write_once(folder / "record.json", record)
    seal(evidence)
    contract = json.loads((ROOT / "contract.json").read_text())
    contract["outputs"] = json.loads((ROOT / "outputs.json").read_text())
    results = []
    executions = []
    for kind, case, cfg, _ in planned:
        record = json.loads((evidence / case["name"] / "record.json").read_text())
        if kind == "positive":
            executions.append(record["status"] == "success")
        try:
            if kind == "positive":
                observation, records = observe_execution(
                    "invoice-total",
                    case["inputs"],
                    record,
                    config=cfg,
                    contract=contract,
                )
                check_operation_order(
                    "invoice-total",
                    case["inputs"],
                    records,
                    observation.roles,
                    contract,
                )
                invoice_total(case["inputs"], observation)
            else:
                check_rejection(record, case, cfg, contract)
            results.append({"name": case["name"], "passed": True})
        except Rejected as error:
            results.append({"name": case["name"], "passed": False, "reason": str(error)})
    passed = all(case["passed"] for case in results)
    reward(
        OUT,
        {
            "status": "complete",
            "execution": all(executions),
            "acceptance": passed,
            "cases": results,
            "normalized_reward": int(passed),
        },
    )


if __name__ == "__main__":
    main()
