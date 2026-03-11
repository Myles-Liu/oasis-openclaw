"""
OpenClaw Agent Backend for OASIS

将 OpenClaw session 封装为 OASIS agent 的"大脑"。
每个 agent 对应一个持久化的 OpenClaw session，
拥有独立的记忆、人格和完整的 agent loop。

零侵入 OASIS：通过 monkey-patch SocialAgent.perform_action_by_llm 实现。

用法：
    from openclaw_backend import OpenClawBackend
    backend = OpenClawBackend(gateway_url="ws://127.0.0.1:18789")
    backend.attach(env)  # 自动增强所有 agent
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import websockets

logger = logging.getLogger("openclaw-backend")
logging.basicConfig(level=logging.INFO)


@dataclass
class AgentProfile:
    """Agent 人格配置"""
    name: str
    personality: str  # 性格描述
    background: str   # 背景故事
    goals: str = ""   # 目标/动机
    language: str = "中文"


@dataclass
class AgentSession:
    """一个 agent 对应的 OpenClaw session"""
    agent_id: int
    profile: AgentProfile
    session_key: Optional[str] = None
    created: bool = False


class OpenClawGateway:
    """
    与 OpenClaw Gateway 通信的客户端。
    使用 WebSocket 协议。
    """

    def __init__(self, gateway_url: str = "ws://127.0.0.1:18789",
                 auth_token: Optional[str] = None):
        self.gateway_url = gateway_url
        self.auth_token = auth_token or self._get_auth_token()
        self._ws = None
        self._msg_id = 0

    def _get_auth_token(self) -> str:
        """从 OpenClaw 配置中读取 auth token"""
        config_path = os.path.expanduser("~/.openclaw/openclaw.json")
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
                return config.get("gateway", {}).get("auth", {}).get("token", "")
        except Exception:
            return ""

    async def connect(self):
        """建立 WebSocket 连接"""
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        self._ws = await websockets.connect(
            self.gateway_url,
            additional_headers=headers,
            ping_interval=30,
        )
        logger.info(f"Connected to OpenClaw Gateway at {self.gateway_url}")

    async def disconnect(self):
        """断开连接"""
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send_message(self, session_key: str, message: str,
                           timeout: float = 60.0) -> str:
        """
        向指定 session 发送消息并等待回复。
        这是最核心的接口——把一个 prompt 发给 OpenClaw session，
        session 内部会自动运行 agent loop（多轮工具调用），
        最终返回结果。
        """
        # 使用 CLI 方式发送（更可靠）
        result = await asyncio.create_subprocess_exec(
            "openclaw", "send", "--session", session_key,
            "--message", message,
            "--timeout", str(int(timeout)),
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await result.communicate()

        if result.returncode == 0:
            try:
                data = json.loads(stdout.decode())
                return data.get("reply", stdout.decode().strip())
            except json.JSONDecodeError:
                return stdout.decode().strip()
        else:
            logger.error(f"Send failed: {stderr.decode()}")
            return ""

    async def spawn_session(self, task: str, label: str,
                            mode: str = "session") -> str:
        """
        创建一个新的 OpenClaw session。
        mode="session" 创建持久化 session（可多次对话）。
        mode="run" 创建一次性 session。
        返回 session key。
        """
        result = await asyncio.create_subprocess_exec(
            "openclaw", "session", "spawn",
            "--task", task,
            "--label", label,
            "--mode", mode,
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await result.communicate()

        if result.returncode == 0:
            try:
                data = json.loads(stdout.decode())
                return data.get("sessionKey", "")
            except json.JSONDecodeError:
                return ""
        else:
            logger.error(f"Spawn failed: {stderr.decode()}")
            return ""


class OpenClawBackend:
    """
    将 OpenClaw session 作为 OASIS agent 的后端。

    工作原理：
    1. attach(env) 时，为每个 agent 创建一个 OpenClaw session
    2. monkey-patch perform_action_by_llm，替换为 OpenClaw 驱动的决策
    3. 每次 step 时，把环境信息发给 OpenClaw session
    4. OpenClaw 内部运行完整的 agent loop，返回决策结果
    5. 解析结果，调用 OASIS 的 action 执行
    """

    def __init__(self,
                 profiles: Optional[Dict[int, AgentProfile]] = None,
                 soul_template: str = "",
                 max_concurrent: int = 5):
        """
        Args:
            profiles: agent_id -> AgentProfile 映射，为空则从 OASIS 的 user_info 生成
            soul_template: SOUL.md 模板，{name} {personality} {background} {goals} 会被替换
            max_concurrent: 最大并发 session 数
        """
        self.profiles = profiles or {}
        self.soul_template = soul_template or DEFAULT_SOUL_TEMPLATE
        self.max_concurrent = max_concurrent
        self.sessions: Dict[int, AgentSession] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._attached = False

    def attach(self, env):
        """
        零侵入接入 OASIS 环境。
        为每个 agent 创建 session 并 patch perform_action_by_llm。
        """
        from oasis.social_agent.agent import SocialAgent

        agents = list(env.agent_graph.get_agents())
        logger.info(f"Attaching OpenClaw backend to {len(agents)} agents")

        for agent_id, agent in agents:
            if not isinstance(agent, SocialAgent):
                continue

            # 创建 AgentProfile
            profile = self.profiles.get(agent_id)
            if not profile:
                # 从 OASIS 的 user_info 自动生成
                user_info = agent.user_info
                profile = AgentProfile(
                    name=getattr(user_info, 'name', f'Agent_{agent_id}'),
                    personality=getattr(user_info, 'description', ''),
                    background=getattr(user_info, 'bio', ''),
                )

            session = AgentSession(agent_id=agent_id, profile=profile)
            self.sessions[agent_id] = session

            # Monkey-patch perform_action_by_llm
            original_perform = agent.perform_action_by_llm
            agent._original_perform = original_perform
            agent._openclaw_session = session
            agent._openclaw_backend = self

            async def patched_perform(a=agent, s=session):
                return await self._perform_with_openclaw(a, s)

            agent.perform_action_by_llm = patched_perform

        self._attached = True
        logger.info(f"OpenClaw backend attached to {len(self.sessions)} agents")

    async def initialize_sessions(self):
        """
        为所有 agent 创建 OpenClaw session。
        在 env.reset() 之后、第一次 env.step() 之前调用。
        """
        tasks = []
        for agent_id, session in self.sessions.items():
            tasks.append(self._create_session(session))

        await asyncio.gather(*tasks)
        logger.info(f"Initialized {len(self.sessions)} OpenClaw sessions")

    async def _create_session(self, session: AgentSession):
        """为一个 agent 创建 OpenClaw session"""
        soul = self.soul_template.format(
            name=session.profile.name,
            personality=session.profile.personality,
            background=session.profile.background,
            goals=session.profile.goals,
            language=session.profile.language,
        )

        task = f"""你现在是一个社交平台上的虚拟用户。以下是你的人格设定：

{soul}

你将收到社交平台的环境信息（推荐帖子、粉丝、群组消息等），
你需要根据自己的性格和偏好，决定要执行什么社交动作。

可用的动作：
- create_post(content): 发帖
- like_post(post_id): 点赞
- repost(post_id): 转发
- follow(user_id): 关注
- create_comment(post_id, content): 评论
- do_nothing(): 不做任何事

请始终以 JSON 格式回复你的决策：
{{"action": "动作名", "args": {{参数}}}}

如果你想发帖或评论，内容要符合你的性格特征，用{session.profile.language}写。
如果当前没有感兴趣的内容，使用 do_nothing。
"""

        # 使用 subprocess 调用 openclaw CLI 创建 session
        # 注意：这里不能直接调用 sessions_spawn 工具，
        # 因为我们在一个独立的 Python 脚本中运行
        label = f"oasis-agent-{session.agent_id}"
        result = subprocess.run(
            ["openclaw", "session", "spawn",
             "--task", task,
             "--label", label,
             "--mode", "session"],
            capture_output=True, text=True, timeout=30,
        )

        if result.returncode == 0:
            # 解析 session key
            for line in result.stdout.split("\n"):
                if "sessionKey" in line or "session" in line.lower():
                    try:
                        data = json.loads(result.stdout)
                        session.session_key = data.get("childSessionKey", "")
                    except json.JSONDecodeError:
                        pass
            session.created = True
            logger.info(f"Created session for agent {session.agent_id}: "
                        f"{session.session_key}")
        else:
            logger.error(f"Failed to create session for agent "
                         f"{session.agent_id}: {result.stderr}")

    async def _perform_with_openclaw(self, agent, session: AgentSession):
        """
        使用 OpenClaw session 替代原始的 LLM 决策。
        
        流程：
        1. 获取环境 prompt（帖子、粉丝等）
        2. 发给 OpenClaw session
        3. OpenClaw 内部运行 agent loop
        4. 解析返回的 action
        5. 通过 OASIS channel 执行 action
        """
        async with self._semaphore:
            try:
                # 1. 获取环境信息
                env_prompt = await agent.env.to_text_prompt()

                # 2. 构造消息
                message = (
                    f"以下是你当前看到的社交平台环境：\n\n"
                    f"{env_prompt}\n\n"
                    f"请根据你的性格决定下一步动作。"
                    f"用 JSON 格式回复：{{\"action\": \"动作名\", \"args\": {{参数}}}}"
                )

                # 3. 发给 OpenClaw session
                if not session.session_key:
                    logger.warning(f"Agent {session.agent_id} has no session, "
                                   f"falling back to original perform")
                    return await agent._original_perform()

                reply = await self._send_to_session(
                    session.session_key, message
                )

                if not reply:
                    logger.warning(f"Agent {session.agent_id} got empty reply, "
                                   f"using do_nothing")
                    return None

                # 4. 解析 action
                action = self._parse_action(reply)

                if not action or action["action"] == "do_nothing":
                    logger.info(f"Agent {session.agent_id} decided to do nothing")
                    return None

                # 5. 执行 action
                result = await self._execute_action(agent, action)
                logger.info(f"Agent {session.agent_id} performed "
                            f"{action['action']}: {result}")
                return result

            except Exception as e:
                logger.error(f"Agent {session.agent_id} error: {e}")
                return None

    async def _send_to_session(self, session_key: str, message: str) -> str:
        """通过 OpenClaw CLI 发送消息到 session"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "openclaw", "send",
                "--session-key", session_key,
                message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=60
            )
            return stdout.decode().strip()
        except asyncio.TimeoutError:
            logger.error(f"Session {session_key} timed out")
            return ""
        except Exception as e:
            logger.error(f"Send to session failed: {e}")
            return ""

    def _parse_action(self, reply: str) -> Optional[Dict[str, Any]]:
        """从 OpenClaw 回复中解析 action JSON"""
        # 尝试直接解析
        try:
            return json.loads(reply)
        except json.JSONDecodeError:
            pass

        # 尝试从文本中提取 JSON（支持嵌套 {} 的 args）
        import re
        json_match = re.search(r'\{[^{}]*"action"\s*:\s*"[^"]*"\s*,\s*"args"\s*:\s*\{[^}]*\}[^}]*\}', reply)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # 尝试从 code block 中提取
        code_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', reply, re.DOTALL)
        if code_match:
            try:
                return json.loads(code_match.group(1))
            except json.JSONDecodeError:
                pass

        logger.warning(f"Failed to parse action from reply: {reply[:200]}")
        return None

    async def _execute_action(self, agent, action: Dict[str, Any]):
        """通过 OASIS 的 channel 执行 action"""
        action_name = action.get("action", "")
        args = action.get("args", {})

        action_map = {
            "create_post": agent.env.action.create_post,
            "like_post": agent.env.action.like,
            "repost": agent.env.action.repost,
            "follow": agent.env.action.follow,
            "create_comment": agent.env.action.create_comment,
            "send_message_to_group": agent.env.action.send_message_to_group,
        }

        func = action_map.get(action_name)
        if func:
            return await func(**args)
        else:
            logger.warning(f"Unknown action: {action_name}")
            return None

    def detach(self, env):
        """恢复原始的 perform_action_by_llm"""
        for agent_id, agent in env.agent_graph.get_agents():
            if hasattr(agent, '_original_perform'):
                agent.perform_action_by_llm = agent._original_perform
                del agent._original_perform
                del agent._openclaw_session
                del agent._openclaw_backend
        self._attached = False
        logger.info("OpenClaw backend detached")


DEFAULT_SOUL_TEMPLATE = """# 我是 {name}

## 性格
{personality}

## 背景
{background}

## 目标
{goals}

## 行为准则
- 我用{language}交流
- 我的发言风格要符合我的性格
- 我会根据真实感受做出选择，不会机械地点赞每一条帖子
- 如果看到有趣的内容我会互动，无聊的内容我会跳过
- 我有自己的观点和偏好
"""


# === 快捷入口 ===

async def run_oasis_with_openclaw(
    agent_graph,
    platform,
    profiles: Optional[Dict[int, AgentProfile]] = None,
    num_rounds: int = 5,
    max_concurrent: int = 5,
):
    """
    一键运行 OASIS + OpenClaw Backend。

    Usage:
        import asyncio
        from openclaw_backend import run_oasis_with_openclaw, AgentProfile

        profiles = {
            0: AgentProfile(name="小明", personality="开朗外向，爱分享美食"),
            1: AgentProfile(name="小红", personality="安静内向，喜欢读书"),
        }
        asyncio.run(run_oasis_with_openclaw(agent_graph, platform, profiles))
    """
    import oasis

    # 1. 创建环境
    env = oasis.make(agent_graph=agent_graph, platform=platform)
    await env.reset()

    # 2. 接入 OpenClaw Backend
    backend = OpenClawBackend(
        profiles=profiles,
        max_concurrent=max_concurrent,
    )
    backend.attach(env)
    await backend.initialize_sessions()

    # 3. 运行
    from oasis import LLMAction
    for round_num in range(num_rounds):
        logger.info(f"\n{'='*50}\nRound {round_num + 1}/{num_rounds}\n{'='*50}")

        actions = {
            agent: LLMAction()
            for _, agent in env.agent_graph.get_agents()
        }
        await env.step(actions)

        # 等待一下，避免 rate limit
        await asyncio.sleep(2)

    # 4. 清理
    backend.detach(env)
    await env.close()
