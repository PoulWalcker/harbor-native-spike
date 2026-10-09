import threading
from http.server import ThreadingHTTPServer
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from native_spike.contracts import CompiledWorkflow, CompileOptions, RunBinding
from native_spike.execute.n8n import INHERITED_ENVIRONMENT, binding_environment
from tests.support import ROOT, SERVER


def test_candidate_token_is_scoped_admin_routes_deny_and_cross_run_tokens_fail(
    tmp_path,
):
    world = SERVER.World(Mock(), {"limits": {"wall_clock_seconds": 30}})
    other = SERVER.World(Mock(), {"limits": {"wall_clock_seconds": 30}})
    server = ThreadingHTTPServer(("127.0.0.1", 0), SERVER.handler_for(world))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    opener = build_opener(ProxyHandler({}))
    try:
        for route, token in [
            ("/prepare", world._candidate_token),
            ("/snapshot", world._candidate_token),
            ("/tools", other._candidate_token),
        ]:
            request = Request(
                f"http://127.0.0.1:{server.server_port}" + route,
                data=b"{}",
                headers={"Authorization": "Bearer " + token},
            )
            with pytest.raises(HTTPError) as error:
                opener.open(request, timeout=2)
            assert error.value.code == 401
        assert world.started is None
        assert world._candidate_token != world._admin_token
    finally:
        server.shutdown()
        server.server_close()


def test_n8n_inherits_only_tool_credential_and_no_admin_or_host_secrets():
    compiled = CompiledWorkflow(
        "n8n",
        {},
        {},
        120,
        CompileOptions(operation_url="http://simulator:8000/tools", bound_deadline=True),
    )
    env = binding_environment(compiled, RunBinding(deadline_at=100, operation_token="tool-only"))
    assert env["SAPI_OPERATION_TOKEN"] == "tool-only"
    assert all("admin" not in k.lower() for k in env)
    assert set(INHERITED_ENVIRONMENT) == {
        "PATH",
        "HOME",
        "TMPDIR",
        "TZ",
        "LANG",
        "LC_ALL",
    }


def test_context_allowlists_and_public_private_separation():
    assert (ROOT / ".dockerignore").read_text().splitlines()[0] == "*"
    assert "COPY src" not in (ROOT / "infra/agent.Dockerfile").read_text()
    assert "FROM harbor-native-runtime" not in (ROOT / "infra/agent.Dockerfile").read_text()
    for name in ["invoice-total", "checkout-recovery"]:
        task = ROOT / "tasks" / name
        assert not list((task / "environment").rglob("config.yaml"))
        assert not list((task / "tests").glob("config.yaml"))
        assert not list((task / "environment").rglob("evaluation"))
        assert (task / "environment/.dockerignore").read_text().startswith("*\n")
        assert "network_mode: none" in (task / "environment/docker-compose.yaml").read_text()
        assert 'environment_mode = "separate"' in (task / "task.toml").read_text()
    compose = (ROOT / "tasks/checkout-recovery/tests/docker-compose.yaml").read_text()
    assert "internal: true" in compose and "ports:" not in compose and "docker.sock" not in compose
