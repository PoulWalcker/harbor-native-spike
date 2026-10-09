"""Integration assertions around the native Harbor CLI, never a replacement runner."""

import hashlib
import json
import os
import subprocess
import tarfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.support import ROOT

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("RUN_DOCKER") != "1",
        reason="Set RUN_DOCKER=1 for real unpaid Harbor trials",
    ),
]


def run(task, agent, *args):
    name = "test-" + task + "-" + uuid.uuid4().hex[:8]
    command = [
        "harbor",
        "run",
        "-p",
        str(ROOT / "tasks" / task),
        "-a",
        agent,
        "--max-retries",
        "0",
        "--job-name",
        name,
        "--jobs-dir",
        str(ROOT / "jobs"),
        *args,
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        timeout=480,
    )
    (ROOT / "reports" / f"{name}.log").write_text(completed.stdout + completed.stderr)
    trial = next((ROOT / "jobs" / name).glob(task + "__*/result.json"))
    result = json.loads(trial.read_text())
    containers = subprocess.check_output(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=" + trial.parent.name.lower() + "__env",
        ],
        text=True,
    ).strip()
    assert not containers, "Harbor left the agent environment running"
    for suffix in ["__verifier__trial", "__env"]:
        project = trial.parent.name.lower() + suffix
        assert not subprocess.check_output(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project=" + project,
            ],
            text=True,
        ).strip()
        assert not subprocess.check_output(
            [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                "label=com.docker.compose.project=" + project,
            ],
            text=True,
        ).strip()
        assert not subprocess.check_output(
            [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                "label=com.docker.compose.project=" + project,
            ],
            text=True,
        ).strip()
    return trial.parent, result


@pytest.mark.parametrize(
    "task,agent,expected",
    [
        ("invoice-total", "oracle", 1.0),
        ("invoice-total", "nop", 0.0),
        ("checkout-recovery", "oracle", 0.732),
        ("checkout-recovery", "nop", None),
    ],
)
def test_native_controls(task, agent, expected):
    trial, result = run(task, agent)
    report = json.loads((trial / "verifier/evaluation.json").read_text())
    assert report["normalized_reward"] == expected
    assert report["acceptance"] is (agent == "oracle")
    if expected is None:
        assert result["verifier_result"] is None or result["verifier_result"]["rewards"] is None
        assert result["exception_info"]["exception_type"] == "RewardFileNotFoundError"
    else:
        assert result["exception_info"] is None
        assert result["verifier_result"]["rewards"] == {"reward": expected}
    if task == "invoice-total" and agent == "oracle":
        assert len(report["cases"]) == 10 and all(c["passed"] for c in report["cases"])
    if agent == "oracle":
        manifest = json.loads((trial / "verifier/evidence-manifest.json").read_text())
        assert all(
            hashlib.sha256((trial / "verifier/evidence" / p).read_bytes()).hexdigest() == sha
            for p, sha in manifest.items()
        )


@pytest.mark.parametrize(
    "candidate,expected_executions",
    [("invoice-wrong.yaml", 10), ("invoice-invalid.yaml", 0)],
)
def test_reject_candidate(candidate, expected_executions):
    trial, result = run(
        "invoice-total",
        "tests.control_agent:ControlAgent",
        "--ak",
        "candidate=tests/fixtures/" + candidate,
    )
    assert result["verifier_result"]["rewards"] == {"reward": 0.0}
    assert len(list((trial / "verifier").glob("evidence/*/record.json"))) == expected_executions


@pytest.mark.parametrize("attack", ["symlink", "fifo", "extra", "artifact"])
def test_hostile_transfer(attack):
    args = ["--ak", "attack=" + attack]
    if attack in ["extra", "artifact"]:
        args += ["--ak", "candidate=tasks/invoice-total/solution/config.yaml"]
    trial, result = run("invoice-total", "tests.control_agent:ControlAgent", *args)
    # Conventional logs/artifacts is excluded by native transfer, so forging there has no effect.
    expected = 1.0 if attack == "artifact" else 0.0
    assert result["verifier_result"]["rewards"] == {"reward": expected}
    assert not (trial / "verifier/reward.txt").is_symlink()
    assert not (trial / "artifacts/discarded-convention/reward.txt").exists()


@pytest.mark.parametrize("mode", ["success", "timeout", "wrong-model", "malformed"])
def test_real_n8n_llm_bridge(mode):
    trial, result = run(
        "checkout-recovery",
        "tests.control_agent:ControlAgent",
        "--ak",
        "candidate=tests/fixtures/checkout-llm.yaml",
        "--ve",
        "SPIKE_FAKE_MODE=" + mode,
    )
    audit = [json.loads(line) for line in (trial / "verifier/evidence/runtime-dispatch.jsonl").read_text().splitlines()]
    attempts = [r for r in audit if r["event"] == "dispatch_attempt"]
    assert len(attempts) == (2 if mode == "success" else 1)
    report = json.loads((trial / "verifier/evaluation.json").read_text())
    assert report["acceptance"] is (mode == "success")
    native = json.loads((trial / "verifier/evidence/native-record.json").read_text())
    assert native["llm"]["agency_http_call_count"] == len(attempts)
    if mode == "success":
        assert result["verifier_result"]["rewards"] == {"reward": 0.732}
        assert native["status"] == "success"
        evidence = json.loads((trial / "verifier/evidence/environment-evidence.json").read_text())
        assert all(evidence["checks"].values())
    else:
        assert native["status"] == "error"
        assert any(r.get("model_outcome") == "unknown" for r in audit)


def test_missing_judge_in_native_result_is_unknown():
    trial, result = run("checkout-recovery", "oracle", "--ve", "SPIKE_JUDGE_MODE=none")
    report = json.loads((trial / "verifier/evaluation.json").read_text())
    assert report["acceptance"] is True
    assert report["normalized_reward"] is None
    assert not (trial / "verifier/reward.txt").exists()
    assert result["exception_info"]["exception_type"] == "RewardFileNotFoundError"


def test_wrapper_agent_native_submission():
    answer = (ROOT / "tasks/invoice-total/solution/config.yaml").read_text()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(
                {
                    "ok": True,
                    "exit_code": 0,
                    "output": answer,
                    "stderr": "model: spike-fake-codex\n",
                }
            ).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        trial, result = run(
            "invoice-total",
            "native_spike.agent:CodexYamlAgent",
            "--model",
            "spike-fake-codex",
            "--ak",
            f"upstream=http://127.0.0.1:{server.server_port}",
        )
        assert result["verifier_result"]["rewards"] == {"reward": 1.0}
        assert len(requests) == 1 and "sapi-lab/v0" in requests[0]["prompt"]
        assert (trial / "artifacts/submission/config.yaml").read_text() == answer
        assert json.loads((trial / "agent/generation.json").read_text())["paid_dispatch"] is False
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("task", ["invoice-total", "checkout-recovery"])
def test_public_image_every_ancestor_layer_has_no_private_material(tmp_path, task):
    image = "harbor-native-public-security:" + task
    subprocess.run(
        ["docker", "build", "-t", image, str(ROOT / "tasks" / task / "environment")],
        check=True,
        capture_output=True,
    )
    archive = tmp_path / "image.tar"
    subprocess.run(["docker", "save", "-o", str(archive), image], check=True)
    markers = [
        (ROOT / "tasks" / task / "solution/config.yaml").read_bytes(),
        b"def invoice_total(",
        b"class World:",
        b"FrozenTaskContract",
        b"checkout-credentials",
    ]
    inspected = 0
    with tarfile.open(archive) as outer:
        manifest = json.load(outer.extractfile("manifest.json"))
        for layer in manifest[0]["Layers"]:
            with tarfile.open(fileobj=outer.extractfile(layer), mode="r|*") as content:
                for entry in content:
                    name = entry.name.removeprefix("./")
                    assert not name.startswith(("tests/", "solution/", "opt/native/", "run/checkout/"))
                    if entry.isfile():
                        data = content.extractfile(entry).read()
                        assert all(marker not in data for marker in markers), name
                        inspected += 1
    assert inspected > 100
    (ROOT / "reports" / ("image-isolation-" + task + ".json")).write_text(
        json.dumps(
            {
                "layers": len(manifest[0]["Layers"]),
                "files_inspected": inspected,
                "private_markers_absent": True,
            },
            indent=2,
        )
    )
