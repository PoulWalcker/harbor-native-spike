import json
import os
import subprocess
import time
import uuid

import pytest

from tests.support import ROOT

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(os.environ.get("RUN_DOCKER") != "1", reason="Set RUN_DOCKER=1"),
]


def test_running_native_mounts_networks_and_agent_boundary():
    name = "security-" + uuid.uuid4().hex[:8]
    observed = {}
    with (ROOT / "reports" / f"{name}.log").open("w") as log:
        process = subprocess.Popen(
            [
                "harbor",
                "run",
                "-p",
                "tasks/checkout-recovery",
                "-a",
                "tests.control_agent:ProbeAgent",
                "--ak",
                "candidate=tasks/checkout-recovery/solution/config.yaml",
                "--job-name",
                name,
                "--max-retries",
                "0",
                "--force-build",
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            stdout=log,
            stderr=log,
        )
        deadline = time.monotonic() + 480
        try:
            while process.poll() is None:
                assert time.monotonic() < deadline
                trial_dirs = list((ROOT / "jobs" / name).glob("checkout-recovery__*"))
                if trial_dirs:
                    prefix = trial_dirs[0].name.lower()
                    lines = subprocess.check_output(
                        [
                            "docker",
                            "ps",
                            "--format",
                            '{{.ID}} {{.Label "com.docker.compose.project"}}',
                        ],
                        text=True,
                    ).splitlines()
                    for line in lines:
                        cid, _, project = line.partition(" ")
                        if not project.startswith(prefix) or cid in observed:
                            continue
                        info = json.loads(subprocess.check_output(["docker", "inspect", cid], text=True))[0]
                        mounts = info["Mounts"]
                        networks = info["NetworkSettings"]["Networks"]
                        assert all("docker.sock" not in m["Source"] for m in mounts)
                        assert all(
                            "sapi-config-lab" not in m["Source"] and "harbor-baseline" not in m["Source"]
                            for m in mounts
                        )
                        if project.endswith("__env"):
                            assert info["HostConfig"]["NetworkMode"] == "none"
                            assert all("/run/checkout" != m["Destination"] for m in mounts)
                            assert all(m["Type"] != "bind" or "/jobs/" in m["Source"] for m in mounts)
                        else:
                            for network in networks:
                                ni = json.loads(
                                    subprocess.check_output(
                                        ["docker", "network", "inspect", network],
                                        text=True,
                                    )
                                )[0]
                                assert ni["Internal"] is True
                            if info["Config"]["Labels"]["com.docker.compose.service"] == "main":
                                assert next(m for m in mounts if m["Destination"] == "/run/checkout")["RW"] is False
                        assert not info["HostConfig"]["Privileged"]
                        observed[cid] = {
                            "project": project,
                            "service": info["Config"]["Labels"]["com.docker.compose.service"],
                            "image": info["Image"],
                            "mounts": mounts,
                            "networks": list(networks),
                            "env_keys": [s.split("=")[0] for s in info["Config"]["Env"]],
                        }
                time.sleep(1)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=30)
    assert len(observed) == 3, observed
    trial = next((ROOT / "jobs" / name).glob("checkout-recovery__*/result.json"))
    assert json.loads(trial.read_text())["verifier_result"]["rewards"] == {"reward": 0.732}
    assert json.loads((trial.parent / "agent/isolation.json").read_text())["egress_blocked"] is True
    for info in observed.values():
        project = info["project"]
        for command in [
            ["docker", "ps", "-aq"],
            ["docker", "network", "ls", "-q"],
            ["docker", "volume", "ls", "-q"],
        ]:
            assert not subprocess.check_output(
                [*command, "--filter", "label=com.docker.compose.project=" + project],
                text=True,
            ).strip()
    (ROOT / "reports/live-isolation.json").write_text(json.dumps(observed, indent=2))


def test_simulator_death_preserves_unknown_and_does_not_retry():
    name = "death-" + uuid.uuid4().hex[:8]
    with (ROOT / "reports" / f"{name}.log").open("w") as log:
        process = subprocess.Popen(
            [
                "harbor",
                "run",
                "-p",
                "tasks/checkout-recovery",
                "-a",
                "oracle",
                "--job-name",
                name,
                "--max-retries",
                "0",
                "--force-build",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=log,
        )
        killed = False
        deadline = time.monotonic() + 400
        try:
            while process.poll() is None and time.monotonic() < deadline:
                windows = list((ROOT / "jobs" / name).glob("checkout-recovery__*/verifier/world/*-window.json"))
                if windows and not killed:
                    trial = windows[0].parents[2]
                    project = trial.name.lower() + "__verifier__trial"
                    ids = subprocess.check_output(
                        [
                            "docker",
                            "ps",
                            "-q",
                            "--filter",
                            "label=com.docker.compose.project=" + project,
                            "--filter",
                            "label=com.docker.compose.service=simulator",
                        ],
                        text=True,
                    ).split()
                    assert len(ids) == 1
                    subprocess.run(["docker", "kill", ids[0]], check=True, capture_output=True)
                    killed = True
                time.sleep(0.1)
            process.wait(timeout=20)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=30)
    assert killed
    result_path = next((ROOT / "jobs" / name).glob("checkout-recovery__*/result.json"))
    trial = result_path.parent
    report = json.loads((trial / "verifier/evaluation.json").read_text())
    assert report["status"] == "not_evaluated" and report["normalized_reward"] is None
    assert report["reason"] == "unknown_outcome"
    assert not (trial / "verifier/evidence/environment-evidence.json").exists()
    assert json.loads((trial / "verifier/evidence/trial.json").read_text())["termination_reason"] == "unknown"
    assert json.loads((ROOT / "jobs" / name / "result.json").read_text())["stats"]["n_retries"] == 0
    (ROOT / "reports/simulator-death.json").write_text(
        json.dumps(
            {"job": name, "evaluation": report, "durable_window": True, "retries": 0},
            indent=2,
        )
    )
