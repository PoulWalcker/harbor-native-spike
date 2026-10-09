import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER = load("checkout_world", "tasks/checkout-recovery/tests/world.py")
HOOKS = load("checkout_snapshot", "tasks/checkout-recovery/tests/snapshot.py")
ADMISSION = load("admission", "public/submission.py")
