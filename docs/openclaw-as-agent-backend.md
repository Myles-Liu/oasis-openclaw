# OpenClaw 作为 Agent Backend 的可行性分析

> 2026-03-11

## 核心问题

为什么 OpenClaw 的 agent 比裸模型聪明得多？能否将这种智能注入 OASIS 的 agent？

## OpenClaw 智能的本质

**OpenClaw 不是让模型变聪明，而是给模型搭了一个完整的"生存环境"。**

裸模型像失忆的天才，每次醒来什么都不记得；OpenClaw 给了它笔记本、工具箱、日程表、和一个知道自己是谁的身份。

### 三种工作方式对比

**裸模型：**
```
用户消息 → LLM → 回复
```

**Plugin 增强（抽出理念）：**
```
用户消息 → 注入记忆到 prompt → LLM → 回复 → 保存记忆
```

**OpenClaw：**
```
用户消息
  → 注入上下文（SOUL + USER + MEMORY + Skills + 历史）
  → LLM 推理 → "我需要先查代码"
  → 调用 exec 工具 → 拿到结果
  → LLM 再推理 → "还需要确认另一个文件"
  → 调用 read 工具 → 拿到结果
  → LLM 再推理 → "现在理解了，改3个文件"
  → 调用 edit 工具 × 3
  → LLM 再推理 → "改完了，提交"
  → 调用 exec → git commit + push
  → 最终回复
```

**一次"对话"包含 10-30 次 LLM 调用 + 工具执行。这不是增强 prompt 能解决的。**

### 7 层增强

1. **记忆系统** — MEMORY.md + 每日笔记 + 向量搜索（embedding + BM25 + MMR + 时间衰减）
2. **人格注入** — SOUL.md + AGENTS.md + USER.md，每个 session 开始时注入
3. **Skills 系统** — 惰性加载，根据任务匹配最相关的 skill 再读取
4. **Agent Loop** — 无限循环直到任务完成，每一步可调用任意工具
5. **Compaction** — 对话快满时自动压缩为摘要，可以聊一整天不丢上下文
6. **多通道 + 多 Agent** — 子 agent 并行，session 隔离
7. **Heartbeat + Cron** — 主动行为，定时检查

### 关键差距

| 能力 | Plugin 能做到 | OpenClaw 做到 |
|------|-------------|-------------|
| 记忆注入到 prompt | ✅ | ✅ |
| 人格/SOUL 注入 | ✅ | ✅ |
| 多轮工具调用 | ❌ 单次 LLM call | ✅ 自动循环直到完成 |
| 中间失败重试 | ❌ | ✅ |
| 通用工具箱 | ❌ | ✅ 文件/终端/浏览器/搜索 |
| 长对话 Compaction | ❌ context 爆了就崩 | ✅ 自动压缩 |
| 跨 session 通信 | ❌ | ✅ 子 agent 协作 |

**关键差距是 "Agent Loop"。没有 session，就没有 agent loop。没有 agent loop，就只是一个加了记忆的 chatbot。**

## Session 是什么

Session 不只是"一段对话"。它是 agent 的**运行时容器**：

```
Session = {
  对话历史（自动压缩），
  系统指令（SOUL + AGENTS + USER + Skills），
  工具集（exec/read/write/browser/...），
  Agent Loop 引擎（LLM → 工具 → LLM → 工具 → ...），
  状态管理（中断恢复、超时、abort），
  通道绑定（消息从哪来、回复到哪去），
}
```

## 两条接入路径

### 路径 1：OpenClaw Session 作为 ModelBackend（推荐）

```python
class OpenClawBackend(BaseModelBackend):
    """
    把 OpenClaw session 封装为 CAMEL 的模型后端。
    每个 agent 对应一个 OpenClaw session。
    """
    def _run(self, messages, response_format, tools):
        response = sessions_send(self.session_key, prompt)
        return self._to_chat_completion(response)
```

**优势：** 每个 agent 自带完整的 OpenClaw 智能（记忆/人格/工具/agent loop）
**劣势：** 性能和成本高，适合小规模（10-30 agent）

### 路径 2：提取理念做轻量 Plugin

不跑 OpenClaw 进程，把设计理念移植到 CAMEL agent 中。

**优势：** 轻量、快速
**劣势：** 缺少 agent loop，智能程度大幅下降

### 路径 3：混合（最佳）

- 关键角色（领袖、主角）→ 路径 1（OpenClaw session）
- 普通群众 → 路径 2（轻量 Plugin）

## 技术实现要点

### OpenClaw Gateway WebSocket API

OpenClaw 通过 WebSocket 与 Gateway 通信，session 管理通过：
- `sessions_spawn` — 创建新 session
- `sessions_send` — 向 session 发消息并等待回复
- `sessions_list` — 列出 session
- `sessions_history` — 获取 session 历史

### CAMEL 的 BaseModelBackend 接口

```python
class BaseModelBackend(ABC):
    def run(self, messages, response_format, tools) -> ChatCompletion: ...
    async def arun(self, messages, response_format, tools) -> ChatCompletion: ...
```

需要把 OpenClaw session 的回复转换为 `ChatCompletion` 格式。

### 并发问题

200 个 agent 同时跑 = 200 个 OpenClaw session 并发。需要测试 Gateway 能否承受。

## 结论

**OpenClaw 的智能 = 好的 prompt 工程 + 持续的 agent loop + 完整的工具箱 + 自动 compaction**

只抽理念效果差很多。要达到 OpenClaw 级别的智能，必须用 OpenClaw session 作为 backend。
