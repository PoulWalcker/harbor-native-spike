"""Record the exact trusted runtime and task bytes inside the immutable verifier image."""

import hashlib
import platform
import subprocess
from importlib.metadata import version
from pathlib import Path

from native_spike.verifier import write_once


def main():
    roots = [Path("/opt/native"), Path("/tests")]
    sources = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for root in roots
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and "vendor" not in p.parts
    }
    submission = Path("/submission/config.yaml")
    write_once(
        Path("/logs/verifier/identity.json"),
        {
            "source_baseline": "872aea76a493a82562dea391371c38de4397d3df",
            "runtime_sources": sources,
            "python": platform.python_version(),
            "pyyaml": version("PyYAML"),
            "n8n": subprocess.check_output(["n8n", "--version"], text=True).strip(),
            "submission_sha256": hashlib.sha256(submission.read_bytes()).hexdigest() if submission.exists() else None,
            "model_roles": {
                "generation": "agent/generation.json or Harbor oracle/nop",
                "runtime": "spike-fake-codex; budget 4; no paid calls",
                "judge": "SIMULATED or missing; budget 1",
            },
        },
    )


if __name__ == "__main__":
    main()
