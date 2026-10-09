"""Physical source lines by responsibility; excludes third-party/generated/cache/evidence files."""

import argparse
import json
from pathlib import Path

EXTENSIONS = {".py", ".js", ".sh", ".yaml", ".yml", ".toml"}


def category(path, original):
    s = path.as_posix()
    if s.startswith("tests/"):
        return "Automated tests"
    if original:
        if s.startswith("verification/") or "/evaluate/" in s or "/evaluation/" in s:
            return "Independent evaluation"
        if s.startswith("benchmarks/") and "/environment/" in s:
            return "Checkout world and transport"
        if s.startswith("benchmarks/"):
            return "Task/domain assets"
        if "/compile/" in s or s.endswith("/profile.py"):
            return "YAML profile and compiler"
        if (
            "/execute/agency" in s
            or s.endswith("/wrapper_audit.py")
            or "/author/" in s
            or s.endswith("/model_wrapper.py")
            or s.endswith("/yaml_agent.py")
        ):
            return "Model integration"
        if s.endswith("/execute/n8n.py"):
            return "n8n execution"
        if "/coordinate/" in s:
            return "Custom coordination and experiments"
        if "/harbor_integration/" in s or "/benchmark" in s or s.startswith("infra/"):
            return "Task loading packaging and Harbor glue"
        return "Other shared/runtime code"
    if (
        s.startswith("src/acceptance/")
        or s.endswith(("checkout_scoring.py", "replay.py"))
        or (s.startswith("tasks/") and any(x in s for x in ["/verify.py", "/business.py"]))
    ):
        return "Independent evaluation"
    if s.endswith(("/world.py", "/snapshot.py")):
        return "Checkout world and transport"
    if "/compile/" in s or s.endswith("/profile.py"):
        return "YAML profile and compiler"
    if "/execute/agency" in s or s.endswith(("agent.py", "fake_transport.py", "wrapper_audit.py")):
        return "Model integration"
    if s.endswith("/execute/n8n.py"):
        return "n8n execution"
    if s.startswith("tasks/"):
        return "Task/domain assets"
    if s.startswith("infra/") or s.startswith("public/"):
        return "Task loading packaging and Harbor glue"
    return "Other shared/runtime code"


def count(root, original):
    totals = {}
    files = {}
    starts = (
        ["src", "verification", "benchmarks", "harbor", "infra", "tests", "generation"]
        if original
        else ["src", "tasks", "infra", "public", "tests"]
    )
    for start in starts:
        for p in (root / start).rglob("*"):
            if (
                not p.is_file()
                or p.suffix not in EXTENSIONS
                and p.name != "Dockerfile"
                and not p.name.endswith(".Dockerfile")
            ):
                continue
            if any(x in p.parts for x in ["vendor", "__pycache__", "calibration"]):
                continue
            relative = p.relative_to(root)
            area = category(relative, original)
            lines = len(p.read_text().splitlines())
            totals[area] = totals.get(area, 0) + lines
            files[str(relative)] = {"area": area, "lines": lines}
    # Root launch scripts and project configuration are maintained source too.
    for p in root.iterdir():
        if p.is_file() and p.suffix in EXTENSIONS:
            area = "Task loading packaging and Harbor glue"
            lines = len(p.read_text().splitlines())
            totals[area] = totals.get(area, 0) + lines
            files[p.name] = {"area": area, "lines": lines}
    json_assets = [
        p
        for start in (["benchmarks"] if original else ["tasks"])
        for p in (root / start).rglob("*.json")
        if "vendor" not in p.parts and "provenance" not in p.parts
    ]
    return {
        "json_assets": {
            "files": len(json_assets),
            "bytes": sum(p.stat().st_size for p in json_assets),
        },
        "areas": totals,
        "total": sum(totals.values()),
        "production_total": sum(v for k, v in totals.items() if k != "Automated tests"),
        "files": files,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    original = count(args.baseline, True)
    spike = count(Path(__file__).resolve().parents[1], False)
    report = {
        "method": "Physical lines including comments/blank lines. Python, JS, shell, YAML, TOML and Dockerfiles; JSON task assets counted separately in bytes. No third-party/generated/cache/historical evidence. Tests separate. Not feature-normalized.",
        "original": original,
        "spike": spike,
        "production_reduction_percent": round(100 * (1 - spike["production_total"] / original["production_total"]), 1),
    }
    print(json.dumps(report, indent=2))
