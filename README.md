# Native Harbor spike

A standalone architectural experiment against `PoulWalcker/benchmarking`, branch `migration/benchmark-ownership`, frozen at **872aea76a493a82562dea391371c38de4397d3df** on 2026-10-09. The original working tree is neither used nor modified. No GitHub remote, upload, paid generation, or paid judge is needed.

`tasks/invoice-total` and `tasks/checkout-recovery` are the benchmark definitions. Harbor 0.21.0 owns agents, Docker/Compose environments, transfer, scheduling, verification, logs, and trial results. There is no scenario descriptor, task loader, materializer, plugin registry, backend registry, or replacement trial runner.

## Install and run

Requires Docker with Compose, Git, uv, and network access for pinned dependencies at installation/build time. Runtime task networks are isolated. Python 3.14.8 and n8n 2.41.5 match the baseline.

```sh
cd harbor-native-spike
uv sync --locked
uv run python infra/fetch_upstream.py
./infra/build.sh
uv run pytest -m 'not docker' -q
RUN_DOCKER=1 uv run pytest -m docker -q
```

The external AutoWFBench world, schemas and scorer are downloaded at their original pinned revision with every file SHA verified. They are not vendored into this Git repository. That dependency preserves the real checkout simulator and original rubric; Harbor supplies neither. Its original source manifest documents the unresolved upstream redistribution license, so the dependency remains a private download.

The two explicit shared images contain public submission admission code and private compiler/runtime code respectively. `infra/build.sh` builds images only. It does not generate tasks, start containers, or orchestrate trials. Rebuild after changing `src/` or `public/`; use `--force-build` to avoid Harbor reusing a task image whose shared base tag changed.

```sh
# Both fresh worlds and native oracle solutions; expected rewards 1.0 and 0.732.
uv run harbor run -p tasks -a oracle -n 2 --max-retries 0 --force-build --job-name oracle

# Invoice nop: known rejection, reward 0.0.
uv run harbor run -p tasks/invoice-total -a nop --max-retries 0 --force-build --job-name invoice-nop

# Checkout nop: acceptance false, quality unknown, no reward file.
uv run harbor run -p tasks/checkout-recovery -a nop --max-retries 0 --force-build --job-name checkout-nop

# Compilable wrong invoice; real execution followed by independent rejection.
PYTHONPATH=. uv run harbor run -p tasks/invoice-total -a tests.control_agent:ControlAgent \
  --ak candidate=tests/fixtures/invoice-wrong.yaml --max-retries 0 --force-build --job-name invoice-wrong

# Real n8n HTTP LLM nodes and the retained Codex bridge, deterministic fake wrapper transport.
PYTHONPATH=. uv run harbor run -p tasks/checkout-recovery -a tests.control_agent:ControlAgent \
  --ak candidate=tests/fixtures/checkout-llm.yaml --max-retries 0 --force-build --job-name runtime-llm

# Repeat the preceding command with these verifier options to exercise failure paths:
# --ve SPIKE_FAKE_MODE=timeout     (also failure, wrong-model, malformed)
# --ve SPIKE_JUDGE_MODE=none       (independent checks remain available; quality is unknown)

uv run harbor view jobs
```

Choose an unused job name when repeating commands. The Python control agent lives in `tests/`, hence `PYTHONPATH=.`. It is only a test agent; it delegates the complete trial lifecycle to Harbor.

The generation adapter is `native_spike.agent:CodexYamlAgent`. It combines the native task instruction with public profile/rules/catalog, reserves one attempt, calls the existing wrapper protocol (`POST {"prompt": ...}` → `ok`, `exit_code`, `output`, `stderr`), checks the reported model, and uploads the exact YAML. It performs no repairs, fence stripping, or direct model API calls. This spike permits explicitly fake loopback transport only. The following test starts a deterministic HTTP wrapper and exercises the adapter inside an actual Harbor trial:

```sh
RUN_DOCKER=1 uv run pytest tests/test_native.py::test_wrapper_agent_native_submission -q
```

Generation, runtime LLM calls, and judging have distinct evidence and budgets. Runtime calls use the baseline Agency contract and fail-closed reservation ledger, with at most four admitted occurrences. The optional upstream **SIMULATED** judge runs at most once. Saved judge responses can be replayed without dispatch. Live paid wrapper behavior, model quality, and a live wrapper-based judge are **NOT VERIFIED**; no paid path is enabled by the experiment commands.

## Responsibilities and evidence

1. Harbor exposes only `environment/public/` and `instruction.md` to the non-root author.
2. The author writes one YAML file. A root collection hook admits a bounded regular file into `/submission`; links, FIFOs, executable files, extra files and YAML aliases are rejected. Native transfer excludes conventional `/logs/artifacts` to prevent reward or payload smuggling.
3. Harbor starts a separate verifier image. It contains no reference solution. Preflight validates and compiles the entire input plan before n8n execution or world preparation.
4. The verifier invokes real n8n with a fresh SQLite database per fixture. Native node records and persisted execution metadata provide evidence; candidate output alone is insufficient.
5. Invoice evaluates all three positive and seven negative original fixtures, including input lineage, operation ordering, intermediate values, safe integer limits, and the native failing operation.
6. Checkout runs the exact pinned stateful AutoWFBench world as a Harbor Compose sidecar. A fresh scoped tool token reaches n8n; the admin token stays in trusted Python. Independent snapshots, tool receipts and original checks drive the unchanged weighted scoring.
7. Execution evidence is sealed before evaluation; SHA manifests detect later edits. Recorded evidence cannot be overwritten through the provided write paths. This is protection against the candidate and accidental edits, not against a hostile machine administrator.

Inside `jobs/<job>/<trial>/`:

| Path | Meaning |
| --- | --- |
| `result.json`, `trial.log` | Authoritative Harbor phase results and errors |
| `artifacts/submission/` | Native transfer of protected YAML or admission rejection |
| `agent/prompt.txt`, `agent/generation.json` | Generation prompt, model, timeout and invocation evidence when using the wrapper adapter |
| `verifier/identity.json` | Trusted source hashes, Python/n8n versions and submission identity |
| `verifier/evidence/` | Frozen native execution, world, transport and runtime model evidence |
| `verifier/evidence-manifest.json` | SHA-256 inventory of original execution evidence |
| `verifier/evaluation.json` | Execution, acceptance and normalized reward; unknown fields remain null |
| `verifier/evaluation/` | Checkout frozen task contract, independent run log, judge reply and original scoring report |
| `verifier/world/` | Durable simulator observations, including those retained on hard service death |
| `verifier/reward.txt` | Native Harbor reward, only when a score exists |

Checkout's oracle reward **0.732** is six deterministic points plus 1.32 points from the original simulated `maybe` judge. It demonstrates scoring calibration, not language-model quality. Invoice reward is the original binary acceptance score. Checkout nop, missing judge, and unknown world outcomes produce no numeric reward. Harbor represents absent reward files as `RewardFileNotFoundError`; its job mean must not be interpreted as a measured zero for those trials. Inspect the nullable evaluation facts alongside the native trial error.

Offline checkout re-evaluation checks the original evidence manifest, regenerates the run log from those observations, verifies the exact upstream contract, and writes derived results separately:

```sh
# Replace TRIAL with an existing checkout trial directory; choose a new output directory.
uv run python -m native_spike.replay --trial TRIAL \
  --source .cache/autowfbench \
  --manifest tasks/checkout-recovery/tests/provenance/autowfbench-source.json \
  --judgement TRIAL/verifier/evaluation/judge-reply.json --output replay-result
```

Omit `--judgement` to preserve unknown semantic scores. This never reruns the world or dispatches a judge.

## Scope

The domain validator/compiler and independent acceptance code remain separate from Harbor. The checkout transport retains deadlines, reserved receipts, rejection of replay conflicts and ambiguous-outcome latching. The spike intentionally omits the original lifecycle experiments, repair policies, cross-job paid-call ledger and historical report compatibility. Those capabilities were not demonstrated redundant.

`COMPARISON.md` contains the decision, measured code inventory, concrete test evidence, limitations and migration risks. `reports/` contains test logs and compact audit results; larger native trial artifacts remain under ignored `jobs/`. `provenance/` records the fixed baseline and reused files. The wheel is independently installable; native task assets remain ordinary directories in the source distribution/checkout.
