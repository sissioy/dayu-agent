"""run.json 迁移：补齐并修复 Tavily MCP 代理配置。

包内默认配置已经包含自建 Tavily MCP 代理，但既有工作区执行
``dayu-cli init`` 时不会覆盖用户的 ``run.json``。本迁移只在
``web_tools_config`` 已存在且缺少 ``tavily_mcp`` 时补入当前默认配置；
对于曾由 Dayu 写入的默认代理配置，仅把错误的 ``tavily_search`` 工具名
修正为代理实际注册的 ``tavily-search``。其他自定义 MCP 配置保持不变。
"""

from __future__ import annotations

from pathlib import Path

from dayu.cli.workspace_migrations.run_json_utils import (
    JsonObject,
    JsonValue,
    as_json_object,
    load_json_value,
    write_json_value,
)


_RUN_JSON_FILENAME = "run.json"
_WEB_TOOLS_CONFIG_KEY = "web_tools_config"
_TAVILY_MCP_CONFIG_KEY = "tavily_mcp"
_COMMAND_KEY = "command"
_ARGS_KEY = "args"
_ENV_KEY = "env"
_TOOL_NAME_KEY = "tool_name"
_DEFAULT_COMMAND = "npx"
_DEFAULT_PROXY_URL = "https://tavily-proxy.sissioy.workers.dev/mcp"
_DEFAULT_AUTH_HEADER = "x-api-key:${AUTH_KEY}"
_DEFAULT_AUTH_ENV_NAME = "AUTH_KEY"
_DEFAULT_AUTH_ENV_PLACEHOLDER = "{{AUTH_KEY}}"
_LEGACY_TOOL_NAME = "tavily_search"
_CURRENT_TOOL_NAME = "tavily-search"


def _build_default_tavily_mcp_config() -> JsonObject:
    """构造当前自建 Tavily MCP 代理的默认配置。

    Args:
        无。

    Returns:
        可写入 ``web_tools_config.tavily_mcp`` 的 JSON 对象。

    Raises:
        无。
    """

    arguments: list[JsonValue] = [
        "-y",
        "mcp-remote",
        _DEFAULT_PROXY_URL,
        "--header",
        _DEFAULT_AUTH_HEADER,
    ]
    environment: JsonObject = {
        _DEFAULT_AUTH_ENV_NAME: _DEFAULT_AUTH_ENV_PLACEHOLDER,
    }
    return {
        _COMMAND_KEY: _DEFAULT_COMMAND,
        _ARGS_KEY: arguments,
        _ENV_KEY: environment,
        _TOOL_NAME_KEY: _CURRENT_TOOL_NAME,
    }


def _uses_dayu_default_proxy(config: JsonObject) -> bool:
    """判断 MCP 配置是否指向 Dayu 默认自建代理。

    Args:
        config: 待检查的 Tavily MCP JSON 配置。

    Returns:
        参数列表包含 Dayu 默认代理 URL 时返回 True，否则返回 False。

    Raises:
        无。
    """

    arguments = config.get(_ARGS_KEY)
    if not isinstance(arguments, list):
        return False
    return _DEFAULT_PROXY_URL in arguments


def has_tavily_mcp_config(config_dir: Path) -> bool:
    """判断工作区 ``run.json`` 是否声明了 Tavily MCP 配置。

    Args:
        config_dir: 工作区配置目录，即 ``workspace/config``。

    Returns:
        ``web_tools_config.tavily_mcp`` 为 JSON 对象时返回 True；文件缺失、
        JSON 非法或结构不匹配时返回 False。

    Raises:
        无。该函数只服务 init 交互提示，读取失败按未配置处理。
    """

    run_json_path = config_dir / _RUN_JSON_FILENAME
    if not run_json_path.exists():
        return False
    try:
        payload = load_json_value(run_json_path)
    except (OSError, ValueError):
        return False
    payload_obj = as_json_object(payload)
    if payload_obj is None:
        return False
    web_tools_config = as_json_object(payload_obj.get(_WEB_TOOLS_CONFIG_KEY))
    if web_tools_config is None:
        return False
    return as_json_object(web_tools_config.get(_TAVILY_MCP_CONFIG_KEY)) is not None


def migrate_run_json_add_tavily_mcp(config_dir: Path) -> bool:
    """为既有工作区补齐或修复 Tavily MCP 代理配置。

    Args:
        config_dir: 工作区配置目录，即 ``workspace/config``。

    Returns:
        True 表示实际改写了文件；False 表示无需变更、父配置不存在或文件不存在。

    Raises:
        OSError: 读取或写入 ``run.json`` 失败时抛出，由 init 命令显式失败。
        json.JSONDecodeError: ``run.json`` 已存在但 JSON 解析失败时抛出。
    """

    run_json_path = config_dir / _RUN_JSON_FILENAME
    if not run_json_path.exists():
        return False

    payload = load_json_value(run_json_path)
    payload_obj = as_json_object(payload)
    if payload_obj is None:
        return False

    web_tools_config = as_json_object(payload_obj.get(_WEB_TOOLS_CONFIG_KEY))
    if web_tools_config is None:
        return False

    if _TAVILY_MCP_CONFIG_KEY not in web_tools_config:
        web_tools_config[_TAVILY_MCP_CONFIG_KEY] = _build_default_tavily_mcp_config()
        write_json_value(run_json_path, payload)
        return True

    tavily_mcp_config = as_json_object(web_tools_config.get(_TAVILY_MCP_CONFIG_KEY))
    if tavily_mcp_config is None:
        return False

    if (
        tavily_mcp_config.get(_TOOL_NAME_KEY) == _LEGACY_TOOL_NAME
        and _uses_dayu_default_proxy(tavily_mcp_config)
    ):
        tavily_mcp_config[_TOOL_NAME_KEY] = _CURRENT_TOOL_NAME
        write_json_value(run_json_path, payload)
        return True

    return False
