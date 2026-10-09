import asyncio
import copy
import json
import os
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

from native_spike import checkout_scoring as scoring
from native_spike.agent import CodexYamlAgent
from native_spike.contracts import CompileOptions, OutputArtifact
from native_spike.execute.agency import ContractError, DispatchAudit, execute
from native_spike.fake_transport import transport_for
from native_spike.pinned_source import PinnedSource
from native_spike.profile import read
from native_spike.verifier import compile_case
from tests.support import ADMISSION, ROOT

SOURCE = PinnedSource(
    ROOT / "tasks/checkout-recovery/tests/provenance/autowfbench-source.json",
    ROOT / ".cache/autowfbench",
)


def test_historical_calibration_exact_and_missing_judge_unknown(tmp_path):
    saved = {
        n: json.loads((ROOT / "tests/calibration" / f"{n}.json").read_text())
        for n in ["task-contract", "run-log", "judge-reply", "evaluation"]
    }
    contract = scoring.freeze_contract(
        SOURCE,
        "production-checkout-recovery",
        judge_mode="demo",
        artifact=OutputArtifact("incident_summary", "incident-summary.md"),
    )
    assert contract.as_dict() == saved["task-contract"]
    result = scoring.evaluate_once(contract, saved["run-log"], tmp_path / "scored", judgement=saved["judge-reply"])
    assert result["normalized_reward"] == 0.732
    assert result["deterministic_points"] == 6
    assert result["execution_pass"] is True
    assert {k: v for k, v in result.items() if k != "project_acceptance"} == {
        k: v for k, v in saved["evaluation"].items() if k != "project_acceptance"
    }
    result = scoring.evaluate_once(contract, saved["run-log"], tmp_path / "unknown")
    assert result["normalized_reward"] is None
    assert result["execution_pass"] is True
    assert not (tmp_path / "unknown/reward.txt").exists()
    bad = copy.deepcopy(saved["judge-reply"])
    bad["provenance"]["model"] = "foreign"
    with pytest.raises(ValueError):
        scoring.evaluate_once(contract, saved["run-log"], tmp_path / "foreign", judgement=bad)


def test_decimal_normalization_all_reachable_scores():
    for step in range(1001):
        value = float(Decimal(step) / 100)
        assert Decimal(str(scoring.normalized_reward(value))) == Decimal(str(value)) / 10


@pytest.mark.parametrize(
    "status,total,reward",
    [
        ("judge_failed", None, 0),
        ("complete", 10, 0.7),
        ("complete", 10, None),
        ("complete", 10, True),
    ],
)
def test_normalized_evaluation_rejects_inconsistent_scores(tmp_path, status, total, reward):
    with pytest.raises(ValueError):
        scoring.write_evaluation(
            tmp_path,
            {"status": status, "score_0_10": total, "normalized_reward": reward},
        )


@pytest.mark.parametrize(
    "attack",
    [
        "symlink",
        "hardlink",
        "fifo",
        "directory",
        "executable",
        "oversize",
        "extra",
        "recursive",
        "python-tag",
    ],
)
def test_submission_rejection(tmp_path, attack):
    folder = tmp_path / "submission"
    folder.mkdir()
    p = folder / "config.yaml"
    if attack == "symlink":
        p.symlink_to("/etc/passwd")
    elif attack == "hardlink":
        other = tmp_path / "other"
        other.write_text("a: b")
        os.link(other, p)
    elif attack == "fifo":
        os.mkfifo(p)
    elif attack == "directory":
        p.mkdir()
    else:
        p.write_text(
            {
                "oversize": "a" * 1_048_577,
                "recursive": "a: &a [*a]",
                "python-tag": "!!python/object/apply:os.system [echo unsafe]",
            }.get(attack, "a: b")
        )
        if attack == "executable":
            p.chmod(0o755)
        if attack == "extra":
            (folder / "extra").touch()
    with pytest.raises((ValueError, OSError, yaml.YAMLError)):
        ADMISSION.read_submission(folder)


@pytest.mark.parametrize("mode", ["timeout", "failure", "wrong-model", "malformed"])
def test_runtime_bridge_failure_latches_without_retry(tmp_path, mode):
    catalog = read(ROOT / "tasks/checkout-recovery/tests/bindings.yaml")["operations"]
    audit = DispatchAudit(
        tmp_path / "audit.jsonl",
        {
            "max_attempts": 2,
            "operations": {"incident.plan": 2},
            "model": "spike-fake-codex",
        },
    )
    request = {
        "invocation_id": "first",
        "operation": "incident.plan",
        "inputs": {"task": "repair", "evidence": []},
    }
    transport = Mock(wraps=transport_for(tmp_path / "calls", mode))
    with pytest.raises((ContractError, TimeoutError)):
        execute(request, catalog, "fake", 5, audit=audit, transport=transport)
    with pytest.raises(ContractError):
        execute(
            {**request, "invocation_id": "second"},
            catalog,
            "fake",
            5,
            audit=audit,
            transport=transport,
        )
    assert transport.call_count == 1
    assert audit.failed
    assert audit.records()[0]["event"] == "dispatch_attempt"
    assert any(r.get("model_outcome") == "unknown" for r in audit.records())


def test_runtime_budget_reservation_and_identity(tmp_path):
    catalog = read(ROOT / "tasks/checkout-recovery/tests/bindings.yaml")["operations"]
    audit = DispatchAudit(
        tmp_path / "audit.jsonl",
        {
            "max_attempts": 1,
            "operations": {"incident.plan": 1},
            "model": "spike-fake-codex",
        },
    )
    request = {
        "invocation_id": "first",
        "operation": "incident.plan",
        "inputs": {"task": "repair", "evidence": []},
    }
    transport = Mock(wraps=transport_for(tmp_path / "calls"))
    result = execute(request, catalog, "fake", 5, audit=audit, transport=transport)
    assert result["invocation_id"] == "first" and result["output"]["new"] == "charge_card(amount, currency)"
    with pytest.raises(ContractError):
        execute(
            {**request, "invocation_id": "second"},
            catalog,
            "fake",
            5,
            audit=audit,
            transport=transport,
        )
    assert transport.call_count == 1


def test_real_llm_nodes_are_compiled_and_invalid_plan_stops_before_execution():
    cfg = read(ROOT / "tests/fixtures/checkout-llm.yaml")
    bindings = read(ROOT / "tasks/checkout-recovery/tests/bindings.yaml")["operations"]
    compiled = compile_case(
        cfg,
        bindings,
        CompileOptions(
            llm_mode="live",
            bridge_url="http://127.0.0.1:8765",
            operation_url="http://simulator:8000/tools",
        ),
        (ROOT / "tasks/checkout-recovery/tests/operations.js").read_text(),
    )
    nodes = [n for n in compiled.document["nodes"] if n["name"].startswith("Agency ")]
    assert len(nodes) == 2
    assert all(n["type"] == "n8n-nodes-base.httpRequest" and not n.get("retryOnFail") for n in nodes)
    cfg["workflow"]["steps"][0]["uses"] = "shell.exec"
    with pytest.raises(ValueError):
        compile_case(cfg, bindings, CompileOptions(), "")


@pytest.mark.parametrize("fault", ["success", "wrong-model", "failure", "malformed"])
def test_generation_adapter_exact_wrapper_contract(tmp_path, fault):
    answer = (ROOT / "tasks/invoice-total/solution/config.yaml").read_text()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            data = {
                "ok": fault != "failure",
                "exit_code": 1 if fault == "failure" else 0,
                "output": answer if fault != "malformed" else "",
                "stderr": "model: " + ("wrong" if fault == "wrong-model" else "spike-fake-codex") + "\n",
            }
            body = json.dumps(data).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        agent = CodexYamlAgent(
            logs_dir=tmp_path,
            model_name="spike-fake-codex",
            upstream=f"http://127.0.0.1:{server.server_port}",
        )
        env = SimpleNamespace(
            exec=AsyncMock(return_value=SimpleNamespace(return_code=0, stdout="PUBLIC RULES")),
            upload_file=AsyncMock(),
        )
        context = SimpleNamespace(metadata=None)
        if fault == "success":
            asyncio.run(agent.run("TASK", env, context))
            assert env.upload_file.await_count == 1
            assert (tmp_path / "submission.yaml").read_text() == answer
        else:
            with pytest.raises(ValueError):
                asyncio.run(agent.run("TASK", env, context))
            assert env.upload_file.await_count == 0
        assert requests == [{"prompt": "TASK\n\nPUBLIC RULES"}]
        assert (tmp_path / "reservation.json").exists()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    "report",
    [
        {"status": "not_evaluated", "normalized_reward": 0},
        {"status": "complete", "execution": 1, "normalized_reward": 1},
        {"status": "complete", "score_0_10": 7.32, "normalized_reward": 0.73},
        {"status": "complete", "normalized_reward": True},
        {"status": "not_evaluated", "score_0_10": 0, "normalized_reward": None},
    ],
)
def test_native_reward_boundary_rejects_inconsistent_facts(tmp_path, report):
    from native_spike.verifier import reward

    with pytest.raises(ValueError):
        reward(tmp_path, report)
    assert not (tmp_path / "reward.txt").exists()


def test_preflight_enforces_original_task_budget_without_execution(tmp_path, monkeypatch):
    from native_spike import verifier

    config = read(ROOT / "tasks/invoice-total/solution/config.yaml")
    bindings = read(ROOT / "tasks/invoice-total/tests/bindings.yaml")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setattr(
        verifier, "Path", lambda value: artifacts if value == "/logs/artifacts" else tmp_path / "absent"
    )
    monkeypatch.setattr(verifier, "read", lambda value: config if str(value) == "/submission/config.yaml" else bindings)
    verifier.preflight(ROOT, deadline_seconds=30)
    config["execution"]["deadline_seconds"] = 120
    with pytest.raises(ValueError, match="original task deadline"):
        verifier.preflight(ROOT, deadline_seconds=30)
