"""Smoke test verifying MCP stdio JSON-RPC protocol hygiene and 42-tool registration."""

from __future__ import annotations

import json
import subprocess
import sys


def _send_and_read(proc: subprocess.Popen[str], payload: dict, expect_reply: bool = True) -> dict | None:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    if not expect_reply:
        return None
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("EOF from MCP server stdout before receiving reply")
        stripped = line.strip()
        if not stripped:
            continue
        try:
            msg = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"STDOUT CONTAMINATED WITH NON-JSON LINE: {stripped!r}") from exc
        if msg.get("id") == payload.get("id"):
            return msg


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-m", "delft3d_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert proc.stdin is not None and proc.stdout is not None

    try:
        init_res = _send_and_read(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke-test", "version": "0.1.0"},
                },
            },
        )
        assert init_res is not None and "result" in init_res
        _send_and_read(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_reply=False)
        tools_res = _send_and_read(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resources_res = _send_and_read(proc, {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}})
        prompts_res = _send_and_read(proc, {"jsonrpc": "2.0", "id": 4, "method": "prompts/list", "params": {}})
    finally:
        proc.stdin.close()
        stderr_txt = proc.stderr.read() if proc.stderr else ""
        rc = proc.wait(timeout=15)

    if rc != 0:
        print(f"Server exited with code {rc}\nSTDERR:\n{stderr_txt}", file=sys.stderr)
        return 1

    tools = tools_res["result"]["tools"]  # type: ignore[index]
    resources = resources_res["result"]["resources"]  # type: ignore[index]
    prompts = prompts_res["result"]["prompts"]  # type: ignore[index]
    print(
        json.dumps(
            {
                "stdio_clean": True,
                "tools_count": len(tools),
                "resources_count": len(resources),
                "prompts_count": len(prompts),
                "tool_names": [t["name"] for t in tools],
            },
            indent=2,
        )
    )
    assert len(tools) == 45, f"Expected 45 tools, got {len(tools)}"
    assert len(resources) >= 4
    assert len(prompts) == 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
