"""stdio MCP 客户端测试。"""

from __future__ import annotations

import json
import sys

import pytest

from dayu.contracts.cancellation import CancelledError, CancellationToken
from dayu.contracts.tool_configs import McpStdioServerConfig
from dayu.engine.mcp_stdio_client import call_mcp_tool, is_mcp_server_configured


_FAKE_MCP_SERVER = """
import json
import os
import sys

mode = os.environ.get("FAKE_MCP_MODE", "success")
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        if mode == "invalid_json":
            print("not-json", flush=True)
            continue
        response = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "1"},
            },
        }
    elif method == "tools/call":
        if mode == "rpc_error":
            response = {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": -32603, "message": "fake failure"},
            }
        elif mode == "missing_result":
            response = {"jsonrpc": "2.0", "id": message["id"], "result": []}
        else:
            response = {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "content": [{"type": "text", "text": json.dumps(message["params"])}],
                    "isError": False,
                },
            }
    else:
        continue
    print(json.dumps(response), flush=True)
"""


def _build_fake_config(*, mode: str = "success") -> McpStdioServerConfig:
    """构造测试用 stdio MCP 配置。

    Args:
        mode: fake server 响应模式。

    Returns:
        指向内联 Python fake server 的配置。

    Raises:
        无。
    """

    return McpStdioServerConfig(
        command=sys.executable,
        args=("-u", "-c", _FAKE_MCP_SERVER),
        env={"FAKE_MCP_MODE": mode},
        tool_name="tavily-search",
    )


def test_call_mcp_tool_completes_initialize_and_tool_call() -> None:
    """客户端应先握手，再返回远端工具结果。

    Args:
        无。

    Returns:
        无。

    Raises:
        AssertionError: 握手或工具调用结果不符合预期时抛出。
    """

    result = call_mcp_tool(
        config=_build_fake_config(),
        arguments={"query": "腾讯 财报"},
        timeout_seconds=5.0,
    )

    content = result["content"]
    assert isinstance(content, list)
    block = content[0]
    assert isinstance(block, dict)
    text = block["text"]
    assert isinstance(text, str)
    payload = json.loads(text)
    assert payload["name"] == "tavily-search"
    assert payload["arguments"] == {"query": "腾讯 财报"}


def test_is_mcp_server_configured_requires_placeholder_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """环境变量占位符缺失时 auto provider 不应选择 MCP。

    Args:
        monkeypatch: pytest 环境变量替换 fixture。

    Returns:
        无。

    Raises:
        AssertionError: 配置状态判断不符合预期时抛出。
    """

    config = McpStdioServerConfig(
        command="npx",
        args=("mcp-remote", "https://example.com/mcp"),
        env={"AUTH_KEY": "{{AUTH_KEY}}"},
    )
    monkeypatch.delenv("AUTH_KEY", raising=False)
    assert not is_mcp_server_configured(config)

    monkeypatch.setenv("AUTH_KEY", "configured-in-test")
    assert is_mcp_server_configured(config)


def test_call_mcp_tool_honors_precancelled_token() -> None:
    """Host 在启动前取消时不得拉起 MCP 子进程。

    Args:
        无。

    Returns:
        无。

    Raises:
        AssertionError: 预取消调用未抛出取消异常时抛出。
    """

    token = CancellationToken()
    token.cancel()

    with pytest.raises(CancelledError):
        call_mcp_tool(
            config=_build_fake_config(),
            arguments={"query": "cancelled"},
            timeout_seconds=5.0,
            cancellation_token=token,
        )


def test_call_mcp_tool_rejects_invalid_timeout_and_empty_config() -> None:
    """客户端应尽早拒绝非法 timeout，并识别空 MCP 配置。

    Args:
        无。

    Returns:
        无。

    Raises:
        AssertionError: 参数校验结果不符合预期时抛出。
    """

    assert not is_mcp_server_configured(None)
    with pytest.raises(ValueError, match="timeout_seconds 必须大于 0"):
        call_mcp_tool(
            config=_build_fake_config(),
            arguments={"query": "invalid timeout"},
            timeout_seconds=0,
        )


@pytest.mark.parametrize(
    ("mode", "expected_message"),
    [
        ("invalid_json", "非法 JSON"),
        ("rpc_error", "fake failure"),
        ("missing_result", "缺少 result 对象"),
    ],
)
def test_call_mcp_tool_rejects_invalid_protocol_responses(
    mode: str,
    expected_message: str,
) -> None:
    """客户端应把非法 MCP 响应收敛为明确协议错误。

    Args:
        mode: fake server 响应模式。
        expected_message: 预期错误消息片段。

    Returns:
        无。

    Raises:
        AssertionError: 非法响应没有触发预期错误时抛出。
    """

    with pytest.raises(RuntimeError, match=expected_message):
        call_mcp_tool(
            config=_build_fake_config(mode=mode),
            arguments={"query": "invalid response"},
            timeout_seconds=5.0,
        )
