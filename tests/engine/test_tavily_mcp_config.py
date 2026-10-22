"""Tavily MCP 运行配置测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dayu.contracts.tool_configs import build_mcp_stdio_server_config
from dayu.execution.options import (
    build_base_execution_options,
    resolve_web_tools_config_from_toolset_configs,
)


def test_build_base_execution_options_loads_tavily_mcp() -> None:
    """run.json 的 Tavily MCP 配置应进入 web toolset 快照。

    Args:
        无。

    Returns:
        无。

    Raises:
        AssertionError: 配置没有正确进入快照时抛出。
    """

    run_config = {
        "web_tools_config": {
            "provider": "auto",
            "tavily_mcp": {
                "command": "npx",
                "args": ["-y", "mcp-remote", "https://example.com/mcp"],
                "env": {"AUTH_KEY": "{{AUTH_KEY}}"},
                "tool_name": "tavily-search",
            },
        }
    }

    resolved = build_base_execution_options(
        workspace_dir=Path("workspace").resolve(),
        run_config=run_config,
    )
    web_config = resolve_web_tools_config_from_toolset_configs(resolved.toolset_configs)

    assert web_config is not None
    assert web_config.tavily_mcp is not None
    assert web_config.tavily_mcp.command == "npx"
    assert web_config.tavily_mcp.env == {"AUTH_KEY": "{{AUTH_KEY}}"}
    assert web_config.tavily_mcp.tool_name == "tavily-search"


def test_build_mcp_stdio_server_config_rejects_non_string_args() -> None:
    """MCP args 含非字符串元素时应尽早拒绝配置。

    Args:
        无。

    Returns:
        无。

    Raises:
        AssertionError: 非法参数未触发预期异常时抛出。
    """

    with pytest.raises(TypeError, match="args 必须是字符串数组"):
        build_mcp_stdio_server_config(
            {
                "command": "npx",
                "args": ["mcp-remote", 1],
            },
            field_name="web.tavily_mcp",
        )
