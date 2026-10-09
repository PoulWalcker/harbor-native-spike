"""Fetch the exact external evaluator pin for offline tests; task images fetch their own copy."""

from pathlib import Path

from native_spike.pinned_source import PinnedSource, fetch_source

root = Path(__file__).resolve().parents[1]
source = PinnedSource(
    root / "tasks/checkout-recovery/tests/provenance/autowfbench-source.json",
    root / ".cache/autowfbench",
)
fetch_source(source)
print(source.verify()["revision"])
