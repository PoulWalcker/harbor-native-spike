"""Re-evaluate a sealed checkout run without executing n8n, the world, or a model."""

import argparse
import hashlib
import json
from pathlib import Path

from native_spike.checkout_scoring import (
    evaluate_once,
    freeze_contract,
    recorded_run_log,
)
from native_spike.contracts import OutputArtifact
from native_spike.pinned_source import PinnedSource


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--judgement", type=Path)
    args = parser.parse_args()
    root = args.trial / "verifier"
    if args.output.resolve().is_relative_to((root / "evidence").resolve()):
        raise ValueError("Derived evaluation must stay outside original evidence")
    manifest = json.loads((root / "evidence-manifest.json").read_text())
    actual = {
        str(p.relative_to(root / "evidence")): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (root / "evidence").rglob("*")
        if p.is_file()
    }
    if actual != manifest:
        raise ValueError("Recorded evidence changed")
    frozen = json.loads((root / "evaluation/task-contract.json").read_text())
    contract = freeze_contract(
        PinnedSource(args.manifest, args.source),
        "production-checkout-recovery",
        judge_mode=frozen["judge"]["mode"],
        judge_model=frozen["judge"]["model"],
        artifact=OutputArtifact("incident_summary", "incident-summary.md"),
    )
    if contract.as_dict() != frozen:
        raise ValueError("Task/evaluator identity changed")
    run_log = recorded_run_log(contract, root / "evidence")
    if run_log != json.loads((root / "evaluation/run-log.json").read_text()):
        raise ValueError("Derived run log differs from original evidence")
    report = evaluate_once(
        contract,
        run_log,
        args.output,
        judgement=json.loads(args.judgement.read_text()) if args.judgement else None,
    )
    print(json.dumps({"status": report["status"], "reward": report["normalized_reward"]}))


if __name__ == "__main__":
    main()
