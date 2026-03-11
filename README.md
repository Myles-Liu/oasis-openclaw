# oasis-openclaw

> 将 OpenClaw 的完整 Agent 智能注入 OASIS 社交模拟平台

## What

零侵入地把 [OpenClaw](https://github.com/openclaw/openclaw) session 作为 [OASIS](https://github.com/camel-ai/oasis) agent 的"大脑"。每个 agent 不再只是一次 LLM 调用，而是拥有独立记忆、人格、工具箱和持续决策循环的真正智能体。

## Why

OASIS 的 agent 默认使用 CAMEL 的 `ChatAgent.astep()`——**单次 LLM 调用，单轮决策**。

OpenClaw session 是一个完整的 agent 运行时：
- **Agent Loop**：自动循环（推理 → 工具调用 → 推理 → ...），直到任务完成
- **记忆系统**：长期记忆（MEMORY.md）+ 向量搜索 + 时间衰减
- **人格注入**：SOUL.md 定义角色性格，跨轮次一致
- **Compaction**：对话太长时自动压缩，不丢上下文
- **工具箱**：文件系统、终端、浏览器、搜索……不只是社交动作

这是"加了记忆的 chatbot"和"真正的智能体"的区别。

## Quick Start

```python
import oasis
from openclaw_backend import OpenClawBackend, AgentProfile

# 正常创建 OASIS 环境
agent_graph = await generate_twitter_agent_graph(...)
env = oasis.make(agent_graph=agent_graph, platform=platform)
await env.reset()

# 一行接入 OpenClaw
backend = OpenClawBackend(profiles={
    0: AgentProfile(name="小明", personality="开朗外向，爱分享美食"),
    1: AgentProfile(name="小红", personality="安静内向，喜欢读书"),
})
backend.attach(env)           # 自动增强所有 agent
await backend.initialize_sessions()  # 创建 OpenClaw sessions

# 正常运行
for _ in range(5):
    await env.step({agent: LLMAction() for _, agent in env.agent_graph.get_agents()})

# 随时可拔
backend.detach(env)
```

## Architecture

```
OASIS env.step()
  └── SocialAgent.perform_action_by_llm()  [monkey-patched]
        ├── 获取环境 prompt（帖子、粉丝、推荐等）
        ├── 发送到 OpenClaw session
        │     └── OpenClaw Agent Loop（多轮推理 + 工具调用）
        ├── 解析返回的 action JSON
        └── 通过 OASIS Channel 执行社交动作
```

**零侵入**：不修改 OASIS 任何源码，通过 monkey-patch 和 CAMEL 的扩展点实现。

## Docs

- [OASIS 源码分析](docs/oasis-source-analysis.md)
- [OpenClaw 作为 Agent Backend 可行性](docs/openclaw-as-agent-backend.md)
- [插件设计方案](docs/oasis-plugin-design.md)

## Status

🚧 Early prototype — 核心框架完成，正在验证 session 通信流程。

## License

MIT
