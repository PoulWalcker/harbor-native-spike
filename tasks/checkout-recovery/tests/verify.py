"""Native separate-verifier entrypoint: full stateful world, n8n, independent scoring."""

import os
import threading
from http.server import HTTPServer
from pathlib import Path

from snapshot import prepare, snapshot

from native_spike.checkout_scoring import (
    evaluate_once,
    freeze_contract,
    recorded_run_log,
)
from native_spike.contracts import OutputArtifact
from native_spike.execute.agency import make_handler
from native_spike.execute.n8n import execute_compiled
from native_spike.fake_transport import transport_for
from native_spike.pinned_source import PinnedSource
from native_spike.verifier import compile_case, preflight, reward, seal

ROOT = Path(__file__).parent
OUT = Path("/logs/verifier")


def main():
    # Freeze the task/judge before solving; only upstream's explicit unpaid demo judge is enabled.
    contract = freeze_contract(
        PinnedSource(ROOT / "provenance/autowfbench-source.json", ROOT / "vendor/autowfbench"),
        "production-checkout-recovery",
        judge_mode="demo",
        artifact=OutputArtifact("incident_summary", "incident-summary.md"),
    )
    context = {
        "options": {"mode": "live"},
        "evidence": str(OUT / "evidence"),
        "output": str(OUT),
        "submission": "/submission/config.yaml",
    }
    bridge = None
    try:
        config, bindings, options = preflight(ROOT, mode="live", operation_url="http://simulator:8000/tools")
        compiled = compile_case(config, bindings, options, (ROOT / "operations.js").read_text())
    except (ValueError, OSError, KeyError, TypeError) as error:
        # Missing semantic judgement is unknown, including nop; never invent a quality zero.
        reward(
            OUT,
            {
                "status": "not_evaluated",
                "execution": None,
                "acceptance": False,
                "score_0_10": None,
                "normalized_reward": None,
                "reason": type(error).__name__,
            },
        )
        return
    binding = prepare(context)
    try:
        llms = [s for s in config["workflow"]["steps"] if s["kind"] == "LLM"]
        if llms:
            operations = {op: sum(s["uses"] == op for s in llms) for op in {s["uses"] for s in llms}}
            budget = {
                "max_attempts": len(llms),
                "operations": operations,
                "model": "spike-fake-codex",
                "occurrences": {
                    f"{config['workflow']['id']}/r{config['workflow']['revision']}/{s['id']}": s["uses"] for s in llms
                },
                "expires_at": binding.deadline_at,
            }
            bridge = HTTPServer(
                ("127.0.0.1", 8765),
                make_handler(
                    bindings,
                    "fake://codex-exec",
                    10,
                    OUT / "evidence/runtime-dispatch.jsonl",
                    budget,
                    True,
                    transport=transport_for(
                        OUT / "evidence/runtime-model",
                        os.environ.get("SPIKE_FAKE_MODE", "success"),
                    ),
                ),
            )
            threading.Thread(target=bridge.serve_forever, daemon=True).start()
        context["record"] = execute_compiled(compiled, OUT / "evidence/native", binding)
    except (ValueError, OSError, RuntimeError) as error:
        context["record"] = {
            "status": "unknown",
            "output": None,
            "error": type(error).__name__,
        }
    finally:
        trial = snapshot(context)
        if bridge:
            bridge.shutdown()
            bridge.server_close()
    seal(OUT / "evidence")
    if trial["termination_reason"] == "unknown" or not (OUT / "evidence/environment-evidence.json").exists():
        reward(
            OUT,
            {
                "status": "not_evaluated",
                "execution": None,
                "acceptance": None,
                "score_0_10": None,
                "normalized_reward": None,
                "reason": "unknown_outcome",
            },
        )
        return
    run_log = recorded_run_log(contract, OUT / "evidence")
    report = evaluate_once(
        contract,
        run_log,
        OUT / "evaluation",
        dispatch=os.environ.get("SPIKE_JUDGE_MODE", "demo") == "demo",
    )
    reward(
        OUT,
        {
            "status": report["status"],
            "execution": trial["native_execution"],
            "terminal_completion": trial["terminal_completion"],
            "acceptance": report["execution_pass"],
            "score_0_10": report["score_0_10"],
            "normalized_reward": report["normalized_reward"],
            "judge": "SIMULATED; no model quality claim",
        },
    )


if __name__ == "__main__":
    main()
