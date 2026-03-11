"""
OASIS + OpenClaw 编排器

设计思路：
不从外部 Python 脚本调 OpenClaw API，
而是让 OpenClaw agent（主 session）自己当编排器。

流程：
1. 主 agent 用 sessions_spawn 为每个 OASIS agent 创建子 session
2. 每一轮，主 agent 用 sessions_send 把环境 prompt 发给各子 session
3. 子 session 返回 action JSON
4. 主 agent 把 action 传给 OASIS platform 执行

这样每个子 session 都是完整的 OpenClaw agent，拥有记忆、人格、工具。
而编排逻辑在主 agent 的 Python 脚本中运行。

但 sessions_spawn/sessions_send 是 agent 工具，不是 Python SDK。
所以这个脚本需要通过 Gateway WebSocket 调用。

替代方案：写一个 Node.js 脚本直接用 OpenClaw 的内部 API。
"""

# === 方案：Node.js 编排脚本 ===
# 
# OpenClaw 是 Node.js 写的，我们可以直接 import 它的模块：
#
# ```javascript
# import { connectGateway } from '@openclaw/sdk';
# 
# const gw = await connectGateway('ws://127.0.0.1:18789');
# 
# // 为每个 agent 创建 session
# const session = await gw.spawn({ task: '你是小明...', mode: 'session' });
# 
# // 每轮发送环境信息
# const reply = await gw.send(session.key, '当前环境: ...');
# ```
#
# 但 OpenClaw 没有公开的 npm SDK。
#
# === 最终方案：用 OASIS Python + subprocess 调 openclaw acp ===
# 
# `openclaw acp client` 支持 stdio 交互，我们可以通过管道通信。

import asyncio
import json
import subprocess
import sys
from typing import Dict, List, Optional, Tuple


class OpenClawOrchestrator:
    """
    通过 openclaw acp 协议与 Gateway 通信。
    为每个 OASIS agent 维护一个 ACP session。
    """

    def __init__(self):
        self.sessions: Dict[int, dict] = {}  # agent_id -> {process, session_key}

    async def create_agent_session(self, agent_id: int, soul: str) -> str:
        """
        为一个 OASIS agent 创建 OpenClaw session。
        通过 ACP stdio 协议通信。
        """
        # 启动 ACP bridge 进程
        proc = await asyncio.create_subprocess_exec(
            "openclaw", "acp",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self.sessions[agent_id] = {
            "process": proc,
            "agent_id": agent_id,
        }

        # 发送初始化消息（ACP 协议格式）
        init_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-01-01",
                "capabilities": {},
                "clientInfo": {
                    "name": f"oasis-agent-{agent_id}",
                    "version": "0.1.0",
                },
            },
        }

        await self._send_jsonrpc(proc, init_msg)
        response = await self._recv_jsonrpc(proc)

        # 发送 soul/人格设定
        soul_msg = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "sampling/createMessage",
            "params": {
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": soul}},
                ],
                "maxTokens": 500,
            },
        }

        await self._send_jsonrpc(proc, soul_msg)
        response = await self._recv_jsonrpc(proc)

        return f"acp-agent-{agent_id}"

    async def send_prompt(self, agent_id: int, prompt: str) -> str:
        """发送环境 prompt 到 agent session，返回 action"""
        session = self.sessions.get(agent_id)
        if not session:
            return '{"action": "do_nothing", "args": {}}'

        proc = session["process"]

        msg = {
            "jsonrpc": "2.0",
            "id": 100 + agent_id,
            "method": "sampling/createMessage",
            "params": {
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": prompt}},
                ],
                "maxTokens": 500,
            },
        }

        await self._send_jsonrpc(proc, msg)
        response = await self._recv_jsonrpc(proc)

        if response and "result" in response:
            content = response["result"].get("content", {})
            if isinstance(content, dict):
                return content.get("text", "")
            return str(content)
        return '{"action": "do_nothing", "args": {}}'

    async def _send_jsonrpc(self, proc, msg):
        """发送 JSON-RPC 消息"""
        data = json.dumps(msg)
        header = f"Content-Length: {len(data)}\r\n\r\n"
        proc.stdin.write(header.encode() + data.encode())
        await proc.stdin.drain()

    async def _recv_jsonrpc(self, proc, timeout=30):
        """接收 JSON-RPC 响应"""
        try:
            # 读取 Content-Length header
            header = await asyncio.wait_for(
                proc.stdout.readline(), timeout=timeout
            )
            if not header:
                return None

            header_str = header.decode().strip()
            if header_str.startswith("Content-Length:"):
                length = int(header_str.split(":")[1].strip())
                # 读取空行
                await proc.stdout.readline()
                # 读取 body
                body = await proc.stdout.read(length)
                return json.loads(body.decode())
            return None
        except asyncio.TimeoutError:
            return None

    async def cleanup(self):
        """关闭所有 session"""
        for agent_id, session in self.sessions.items():
            proc = session["process"]
            proc.stdin.close()
            await proc.wait()
        self.sessions.clear()
