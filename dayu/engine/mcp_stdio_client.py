"""最小化 stdio MCP 客户端。

该模块只承担单次 MCP 工具调用：启动 stdio server、完成 initialize 握手、
调用一个工具并按 MCP 生命周期关闭进程。进程生命周期不越过单次 Host 工具
调用，因此取消和超时仍由 Host 保持强约束。
"""

from __future__ import annotations

import json
import os
from queue import Empty, Queue
import re
import subprocess
from threading import Thread
import time
from typing import IO, TypeAlias, cast

from dayu.contracts.cancellation import CancellationToken
from dayu.contracts.tool_configs import McpStdioServerConfig


McpJsonScalar: TypeAlias = str | int | float | bool | None
McpJsonValue: TypeAlias = (
    McpJsonScalar | list["McpJsonValue"] | dict[str, "McpJsonValue"]
)
McpJsonObject: TypeAlias = dict[str, McpJsonValue]

_MCP_PROTOCOL_VERSION = "2025-11-25"
_MCP_CLIENT_NAME = "dayu-agent"
_MCP_CLIENT_VERSION = "0.1.4"
_ENV_PLACEHOLDER_PATTERN = re.compile(r"^\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}$")
_RESPONSE_POLL_SECONDS = 0.1
_PROCESS_SHUTDOWN_SECONDS = 0.5
_READER_CLOSED = "__DAYU_MCP_STDIO_READER_CLOSED__"


def _resolve_config_environment(config: McpStdioServerConfig) -> dict[str, str]:
    """解析 MCP 子进程环境变量。

    Args:
        config: stdio MCP 服务配置。

    Returns:
        与当前进程环境合并后的子进程环境。

    Raises:
        RuntimeError: 配置引用的环境变量缺失或为空时抛出。
    """

    resolved = dict(os.environ)
    for name, raw_value in config.env.items():
        match = _ENV_PLACEHOLDER_PATTERN.fullmatch(raw_value.strip())
        if match is None:
            resolved[name] = raw_value
            continue
        source_name = match.group(1)
        source_value = os.environ.get(source_name, "").strip()
        if not source_value:
            raise RuntimeError(f"MCP 环境变量未配置: {source_name}")
        resolved[name] = source_value
    return resolved


def is_mcp_server_configured(config: McpStdioServerConfig | None) -> bool:
    """判断 MCP 配置是否具备启动条件。

    Args:
        config: 可选 stdio MCP 服务配置。

    Returns:
        命令、参数和环境变量占位符都可解析时返回 ``True``。

    Raises:
        无。
    """

    if config is None or not config.command.strip() or not config.args:
        return False
    try:
        _resolve_config_environment(config)
    except RuntimeError:
        return False
    return True


def _read_stdout_lines(stream: IO[str], line_queue: Queue[str]) -> None:
    """持续读取 MCP stdout 并写入队列。

    Args:
        stream: MCP 进程标准输出。
        line_queue: 跨线程传递行文本的队列。

    Returns:
        无。

    Raises:
        无。读取结束通过哨兵通知等待方。
    """

    try:
        for line in stream:
            line_queue.put(line)
    finally:
        line_queue.put(_READER_CLOSED)


def _send_message(process: subprocess.Popen[str], payload: McpJsonObject) -> None:
    """向 MCP 进程发送一条 JSON-RPC 消息。

    Args:
        process: 已启动的 MCP 进程。
        payload: JSON-RPC 消息对象。

    Returns:
        无。

    Raises:
        RuntimeError: 子进程 stdin 不可用时抛出。
        OSError: 写入子进程失败时抛出。
    """

    stream = process.stdin
    if stream is None:
        raise RuntimeError("MCP 子进程 stdin 不可用")
    stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    stream.write("\n")
    stream.flush()


def _wait_for_response(
    *,
    process: subprocess.Popen[str],
    line_queue: Queue[str],
    request_id: int,
    deadline_monotonic: float,
    cancellation_token: CancellationToken | None,
) -> McpJsonObject:
    """等待指定 JSON-RPC 请求的响应。

    Args:
        process: MCP 子进程。
        line_queue: stdout 行队列。
        request_id: 目标 JSON-RPC 请求 ID。
        deadline_monotonic: 整个调用的单调时钟截止点。
        cancellation_token: 可选 Host 取消令牌。

    Returns:
        与请求 ID 匹配的响应对象。

    Raises:
        TimeoutError: 超过 deadline 时抛出。
        RuntimeError: 进程提前退出、响应非法或远端返回 JSON-RPC 错误时抛出。
        CancelledError: Host 已取消当前工具调用时抛出。
    """

    while True:
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("MCP 工具调用超时")
        try:
            line = line_queue.get(timeout=min(_RESPONSE_POLL_SECONDS, remaining))
        except Empty:
            if process.poll() is not None:
                raise RuntimeError(f"MCP 子进程提前退出: exit_code={process.returncode}")
            continue
        if line == _READER_CLOSED:
            raise RuntimeError(f"MCP 子进程关闭输出: exit_code={process.poll()}")
        normalized = line.strip()
        if not normalized:
            continue
        try:
            decoded = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise RuntimeError("MCP 子进程返回了非法 JSON") from exc
        if not isinstance(decoded, dict) or decoded.get("id") != request_id:
            continue
        response = cast(McpJsonObject, decoded)
        error = response.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            raise RuntimeError(f"MCP JSON-RPC 调用失败: {message}")
        return response


def _shutdown_process(process: subprocess.Popen[str]) -> None:
    """按 stdio MCP 生命周期关闭子进程。

    Args:
        process: 待关闭进程。

    Returns:
        无。

    Raises:
        无。关闭阶段错误会被收敛，不覆盖主调用结果。
    """

    try:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        process.wait(timeout=_PROCESS_SHUTDOWN_SECONDS)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.terminate()
        process.wait(timeout=_PROCESS_SHUTDOWN_SECONDS)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
        process.wait(timeout=_PROCESS_SHUTDOWN_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return


def call_mcp_tool(
    *,
    config: McpStdioServerConfig,
    arguments: McpJsonObject,
    timeout_seconds: float,
    cancellation_token: CancellationToken | None = None,
) -> McpJsonObject:
    """启动 stdio MCP 服务并执行一次工具调用。

    Args:
        config: stdio MCP 服务配置。
        arguments: 远端工具参数。
        timeout_seconds: 初始化与调用共享的总超时秒数。
        cancellation_token: 可选 Host 取消令牌。

    Returns:
        MCP ``tools/call`` 的 ``result`` 对象。

    Raises:
        FileNotFoundError: 配置的命令不存在时抛出。
        RuntimeError: 环境、协议或远端工具调用失败时抛出。
        TimeoutError: 初始化或工具调用超时时抛出。
        CancelledError: Host 已取消当前工具调用时抛出。
    """

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()
    process = subprocess.Popen(
        [config.command, *config.args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
        env=_resolve_config_environment(config),
    )
    stdout = process.stdout
    if stdout is None:
        _shutdown_process(process)
        raise RuntimeError("MCP 子进程 stdout 不可用")
    line_queue: Queue[str] = Queue()
    reader = Thread(
        target=_read_stdout_lines,
        args=(stdout, line_queue),
        name="dayu-mcp-stdout",
        daemon=True,
    )
    reader.start()
    deadline_monotonic = time.monotonic() + timeout_seconds
    try:
        _send_message(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": _MCP_CLIENT_NAME,
                        "version": _MCP_CLIENT_VERSION,
                    },
                },
            },
        )
        _wait_for_response(
            process=process,
            line_queue=line_queue,
            request_id=1,
            deadline_monotonic=deadline_monotonic,
            cancellation_token=cancellation_token,
        )
        _send_message(
            process,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
        )
        _send_message(
            process,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": config.tool_name,
                    "arguments": arguments,
                },
            },
        )
        response = _wait_for_response(
            process=process,
            line_queue=line_queue,
            request_id=2,
            deadline_monotonic=deadline_monotonic,
            cancellation_token=cancellation_token,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("MCP tools/call 响应缺少 result 对象")
        return cast(McpJsonObject, result)
    finally:
        _shutdown_process(process)


__all__ = [
    "McpJsonObject",
    "McpJsonValue",
    "call_mcp_tool",
    "is_mcp_server_configured",
]
