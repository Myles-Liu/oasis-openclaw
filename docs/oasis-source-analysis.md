# OASIS 源码深度分析

> 2026-03-11

## 核心架构（4088 行总代码）

```
OasisEnv (编排层)
├── AgentGraph — agent 网络拓扑
├── Platform (1642行) — 社交平台核心，处理所有动作
├── Channel (71行) — Agent↔Platform 的异步消息管道
└── SocialAgent → ChatAgent (CAMEL) — 每个 agent 独立 LLM
```

## 关键代码文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `platform.py` | 1642 | 社交平台核心，28 种 Action，推荐系统 |
| `agent.py` | 300 | SocialAgent，继承 CAMEL ChatAgent |
| `agent_action.py` | 600+ | 社交动作实现（create_post, like, follow...） |
| `agent_environment.py` | 120 | Agent 可见的环境 prompt 生成 |
| `channel.py` | 71 | 异步消息管道（纯队列） |
| `database.py` | 300+ | SQLite 17 张表（user/post/follow/like/comment/group...） |
| `env.py` | 100 | OasisEnv，编排 step/reset/close |
| `typing.py` | 100 | ActionType 枚举 + RecsysType |

## 4 个天然扩展点（零侵入）

### 1. `SocialAgent.__init__(tools=)`
OASIS 会合并自定义 tools 和社交 action tools：
```python
all_tools = (tools or []) + (self.action_tools or [])
```

### 2. `SocialAgent.__init__(model=)`
可以传入自定义 `BaseModelBackend`。

### 3. `ChatAgent.memory` (setter)
CAMEL 的 `AgentMemory` 是抽象类，可替换为自定义实现。

### 4. `generate_agent_graph` 的 profile CSV
自定义人格档案。

## Platform 的硬编码限制

Platform 层完全绑死在社交媒体上：
- 28 种 Action 全是社交动作（create_post, like, follow, repost...）
- 数据库 schema 固定 17 张表
- 推荐系统 4 种（random/reddit/twitter/twhin-bert）都是 feed 推荐
- **这是 OASIS 的天花板**

## CAMEL AgentMemory 体系

```
AgentMemory (抽象基类)
├── ChatHistoryMemory — 纯聊天记录（OASIS 默认使用）
├── VectorDBMemory — 向量检索
└── LongtermAgentMemory — 长短期结合
```

## SocialAgent 核心流程

```python
async def perform_action_by_llm(self):
    # 1. 获取环境 prompt（推荐帖子、粉丝数等）
    env_prompt = await self.env.to_text_prompt()
    
    # 2. 构造用户消息
    user_msg = BaseMessage.make_user_message(content=f"...{env_prompt}")
    
    # 3. 调用 CAMEL 的 astep（单次 LLM + tool calling）
    response = await self.astep(user_msg)
    
    # 4. 执行返回的 tool_calls
    for tool_call in response.info['tool_calls']:
        action_name = tool_call.tool_name
        args = tool_call.args
```

**注意：`max_iteration=1`，只执行一轮 LLM 推理。**

## Channel 是唯一可复用的抽象

Channel 只有 71 行，纯异步消息管道（receive_queue + send_dict）。Agent 通过 Channel 发送 action，Platform 通过 Channel 返回结果。这是唯一不绑定社交媒体的组件。
