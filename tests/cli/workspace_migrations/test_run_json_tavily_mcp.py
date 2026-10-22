"""Tavily MCP ``run.json`` 工作区迁移测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dayu.cli.workspace_migrations.run_json_tavily_mcp import (
    has_tavily_mcp_config,
    migrate_run_json_add_tavily_mcp,
)
from dayu.cli.workspace_migrations.run_json_utils import JsonObject


_DEFAULT_PROXY_URL = "https://tavily-proxy.sissioy.workers.dev/mcp"


def _write_run_json(config_dir: Path, payload: JsonObject) -> Path:
    """写入测试用 ``run.json``。

    Args:
        config_dir: 测试配置目录。
        payload: 待写入的 JSON 顶层对象。

    Returns:
        已写入的 ``run.json`` 路径。

    Raises:
        OSError: 创建目录或写入文件失败时抛出。
        TypeError: payload 无法序列化为 JSON 时抛出。
    """

    config_dir.mkdir(parents=True, exist_ok=True)
    run_json_path = config_dir / "run.json"
    run_json_path.write_text(json.dumps(payload), encoding="utf-8")
    return run_json_path


@pytest.mark.unit
def test_migration_adds_tavily_mcp_to_existing_web_config(tmp_path: Path) -> None:
    """既有 web 配置缺少 MCP 节点时应补入当前默认代理配置。

    Args:
        tmp_path: pytest 临时目录。

    Returns:
        无。

    Raises:
        AssertionError: 迁移结果或保留字段不符合预期时抛出。
    """

    config_dir = tmp_path / "config"
    run_json_path = _write_run_json(
        config_dir,
        {
            "web_tools_config": {
                "provider": "auto",
                "request_timeout_seconds": 12.0,
            },
            "host_config": {"lane": {"llm_api": 8}},
        },
    )

    assert migrate_run_json_add_tavily_mcp(config_dir) is True

    payload = json.loads(run_json_path.read_text(encoding="utf-8"))
    web_config = payload["web_tools_config"]
    assert web_config["provider"] == "auto"
    assert web_config["request_timeout_seconds"] == 12.0
    assert web_config["tavily_mcp"] == {
        "command": "npx",
        "args": [
            "-y",
            "mcp-remote",
            _DEFAULT_PROXY_URL,
            "--header",
            "x-api-key:${AUTH_KEY}",
        ],
        "env": {"AUTH_KEY": "{{AUTH_KEY}}"},
        "tool_name": "tavily-search",
    }


@pytest.mark.unit
def test_migration_repairs_legacy_name_only_for_default_proxy(tmp_path: Path) -> None:
    """Dayu 默认代理的旧下划线工具名应修正并保留其他配置。

    Args:
        tmp_path: pytest 临时目录。

    Returns:
        无。

    Raises:
        AssertionError: 已知错误配置未修复或自定义字段丢失时抛出。
    """

    config_dir = tmp_path / "config"
    run_json_path = _write_run_json(
        config_dir,
        {
            "web_tools_config": {
                "tavily_mcp": {
                    "command": "npx",
                    "args": ["mcp-remote", _DEFAULT_PROXY_URL],
                    "env": {"AUTH_KEY": "{{AUTH_KEY}}"},
                    "tool_name": "tavily_search",
                    "custom_setting": True,
                }
            }
        },
    )

    assert migrate_run_json_add_tavily_mcp(config_dir) is True

    payload = json.loads(run_json_path.read_text(encoding="utf-8"))
    mcp_config = payload["web_tools_config"]["tavily_mcp"]
    assert mcp_config["tool_name"] == "tavily-search"
    assert mcp_config["custom_setting"] is True


@pytest.mark.unit
def test_migration_preserves_custom_mcp_server(tmp_path: Path) -> None:
    """非 Dayu 默认代理的自定义工具名与服务地址不得被改写。

    Args:
        tmp_path: pytest 临时目录。

    Returns:
        无。

    Raises:
        AssertionError: 自定义配置被迁移覆盖时抛出。
    """

    config_dir = tmp_path / "config"
    original: JsonObject = {
        "web_tools_config": {
            "tavily_mcp": {
                "command": "npx",
                "args": ["mcp-remote", "https://mcp.tavily.com/mcp/"],
                "tool_name": "tavily_search",
            }
        }
    }
    run_json_path = _write_run_json(config_dir, original)

    assert migrate_run_json_add_tavily_mcp(config_dir) is False
    assert json.loads(run_json_path.read_text(encoding="utf-8")) == original


@pytest.mark.unit
def test_migration_is_idempotent_for_current_proxy_config(tmp_path: Path) -> None:
    """当前代理工具名已经正确时迁移应保持幂等。

    Args:
        tmp_path: pytest 临时目录。

    Returns:
        无。

    Raises:
        AssertionError: 当前配置仍被重复改写时抛出。
    """

    config_dir = tmp_path / "config"
    original: JsonObject = {
        "web_tools_config": {
            "tavily_mcp": {
                "command": "npx",
                "args": ["mcp-remote", _DEFAULT_PROXY_URL],
                "tool_name": "tavily-search",
            }
        }
    }
    run_json_path = _write_run_json(config_dir, original)

    assert migrate_run_json_add_tavily_mcp(config_dir) is False
    assert has_tavily_mcp_config(config_dir) is True
    assert json.loads(run_json_path.read_text(encoding="utf-8")) == original


@pytest.mark.unit
def test_migration_does_not_create_missing_web_parent(tmp_path: Path) -> None:
    """缺少 web 父配置时不得凭空补出不完整的 web schema。

    Args:
        tmp_path: pytest 临时目录。

    Returns:
        无。

    Raises:
        AssertionError: 迁移错误创建父配置时抛出。
    """

    config_dir = tmp_path / "config"
    run_json_path = _write_run_json(config_dir, {"host_config": {}})

    assert migrate_run_json_add_tavily_mcp(config_dir) is False
    assert has_tavily_mcp_config(config_dir) is False
    assert json.loads(run_json_path.read_text(encoding="utf-8")) == {"host_config": {}}


@pytest.mark.unit
def test_migration_invalid_json_raises_but_presence_check_is_safe(tmp_path: Path) -> None:
    """迁移应对损坏 JSON fail-fast，init 提示探测则应安全返回未配置。

    Args:
        tmp_path: pytest 临时目录。

    Returns:
        无。

    Raises:
        AssertionError: 两个入口的错误语义不符合预期时抛出。
    """

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "run.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        migrate_run_json_add_tavily_mcp(config_dir)
    assert has_tavily_mcp_config(config_dir) is False
