"""One Codex-wrapper call and one YAML upload; Harbor owns the trial."""

import asyncio
import hashlib
import json
import time
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from harbor.agents.base import BaseAgent

from native_spike.net import NoRedirect
from native_spike.verifier import write_once
from native_spike.wrapper_audit import reported_model, tool_markers


class CodexYamlAgent(BaseAgent):
    def __init__(self, *args, upstream: str, transport_kind="fake", timeout=20, **kwargs):
        super().__init__(*args, **kwargs)
        endpoint = urlparse(upstream)
        if transport_kind != "fake" or endpoint.scheme != "http" or endpoint.hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("Spike permits only an explicitly fake loopback wrapper; paid dispatch is disabled")
        self.upstream, self.timeout = upstream, float(timeout)

    @staticmethod
    def name():
        return "codex-wrapper-yaml"

    def version(self):
        return "spike-1"

    async def setup(self, environment):
        pass

    def request(self, prompt):
        request = Request(
            self.upstream,
            json.dumps({"prompt": prompt}).encode(),
            {"Content-Type": "application/json"},
        )
        with build_opener(ProxyHandler({}), NoRedirect).open(request, timeout=self.timeout) as response:
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("Wrapper response too large")
        return json.loads(raw)

    async def run(self, instruction, environment, context):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        public = await environment.exec("cat /app/public/FORMAT.md /app/public/PROFILE.md /app/public/bindings.yaml")
        if public.return_code:
            raise ValueError("Cannot read public authoring contract")
        prompt = instruction + "\n\n" + public.stdout
        (self.logs_dir / "prompt.txt").write_text(prompt)
        record = {
            "role": "generation",
            "transport": "deterministic-fake",
            "paid_dispatch": False,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "model": self.model_name,
            "timeout_seconds": self.timeout,
            "attempts": 1,
            "status": "reserved",
            "outcome": "unknown",
        }
        write_once(self.logs_dir / "reservation.json", record)
        started = time.monotonic()
        try:
            wrapper = await asyncio.to_thread(self.request, prompt)
            write_once(self.logs_dir / "wrapper.json", wrapper)
            if wrapper.get("ok") is not True or type(wrapper.get("exit_code")) is not int or wrapper["exit_code"] != 0:
                raise ValueError("Codex wrapper failed")
            if reported_model(wrapper.get("stderr", "")) != self.model_name or tool_markers(wrapper.get("stderr", "")):
                raise ValueError("Wrapper model or tool policy mismatch")
            answer = wrapper.get("output")
            if not isinstance(answer, str) or not answer.strip() or len(answer.encode()) > 1_048_576:
                raise ValueError("Invalid YAML response")
            path = self.logs_dir / "submission.yaml"
            path.write_text(answer)
            record["output_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            await environment.upload_file(source_path=path, target_path="/app/submission/config.yaml")
            record.update(status="submitted", outcome="completed")
        except Exception as error:
            record.update(status="failed", failure=type(error).__name__)
            raise
        finally:
            record["duration_seconds"] = time.monotonic() - started
            write_once(self.logs_dir / "generation.json", record)
            context.metadata = record
