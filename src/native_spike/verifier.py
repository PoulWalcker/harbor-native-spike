"""Small verifier helpers; Harbor owns trial setup, transfer and teardown."""

import hashlib
import json
from pathlib import Path

from native_spike.compile.n8n import compile_n8n
from native_spike.contracts import CompiledWorkflow, CompileOptions
from native_spike.profile import read, validate


def write_once(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def preflight(root: Path, *, mode="stub", operation_url=None, deadline_seconds=120):
    if any(Path("/logs/artifacts").iterdir()):
        raise ValueError("Undeclared conventional artifact")
    if Path("/submission/rejection.json").exists():
        raise ValueError("Unsafe or missing submission")
    config = read("/submission/config.yaml")
    bindings = read(root / "bindings.yaml")["operations"]
    validate(config, bindings)
    if config["execution"]["deadline_seconds"] != deadline_seconds:
        raise ValueError("The original task deadline is required")
    if "lifecycle" in config or "refinement" in config["execution"]:
        raise ValueError("These two tasks do not admit lifecycle/refinement")
    llms = [s for s in config["workflow"]["steps"] if s["kind"] == "LLM"]
    if len(llms) > 4:
        raise ValueError("Runtime model budget exceeds four")
    options = CompileOptions(
        llm_mode=mode,
        bridge_url="http://127.0.0.1:8765",
        operation_url=operation_url,
        bound_deadline=True,
    )
    return config, bindings, options


def compile_case(config, bindings, options, source):
    from dataclasses import asdict

    document, mapping = compile_n8n(config, bindings, operation_source=source, **asdict(options))
    return CompiledWorkflow("n8n", document, mapping, config["execution"]["deadline_seconds"], options)


def seal(evidence: Path):
    files = {
        str(p.relative_to(evidence)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(evidence.rglob("*"))
        if p.is_file()
    }
    write_once(evidence.parent / "evidence-manifest.json", files)
    for path in evidence.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    evidence.chmod(0o555)


def reward(root: Path, report):
    for field in ("execution", "acceptance"):
        if report.get(field) is not None and type(report[field]) is not bool:
            raise ValueError("Evaluation facts must be booleans or null")
    value = report.get("normalized_reward")
    if report["status"] not in ("complete", "rejected") and value is not None:
        raise ValueError("Missing scores must remain unknown")
    if "score_0_10" in report:
        from native_spike.checkout_scoring import normalized_reward

        total = report["score_0_10"]
        if value is not None and (
            type(total) not in (int, float) or not 0 <= total <= 10 or value != normalized_reward(total)
        ):
            raise ValueError("Score and normalized reward disagree")
        if value is None and total is not None:
            raise ValueError("Unknown normalized reward requires unknown score")
    if value is not None and (type(value) not in (float, int) or not 0 <= value <= 1):
        raise ValueError("Invalid reward")
    write_once(root / "evaluation.json", report)
    if value is not None:
        with (root / "reward.txt").open("x") as stream:
            stream.write(str(value) + "\n")
