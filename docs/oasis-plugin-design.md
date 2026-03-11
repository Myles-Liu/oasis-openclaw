# OASIS 认知增强插件设计方案

> 最后更新：2026-03-11
> 状态：设计中，待宝昌确认

## 设计原则

1. **零侵入** — 不修改 OASIS 仓库任何一行代码
2. **可插拔** — 每个模块独立，随时可换成其他同类实现
3. **渐进式** — 先 B（认知增强），后 C（World Kernel），A 暂缓

---

## 核心发现：OASIS 的扩展点

通过源码分析，OASIS 有 **4 个天然扩展点**，全部支持外部注入，不需要改源码：

### 扩展点 1：`SocialAgent.__init__(tools=)`

```python
SocialAgent(
    tools=[custom_tool_1, custom_tool_2],  # ← 额外 FunctionTool 列表
    ...
)
```

OASIS 会把自定义 tools 和社交 action tools 合并：
```python
all_tools = (tools or []) + (self.action_tools or [])
```

**用途**：给 agent 注入认知能力（回忆、反思、规划等）

### 扩展点 2：`SocialAgent.__init__(model=)`

```python
SocialAgent(
    model=custom_model_backend,  # ← 自定义模型后端
    ...
)
```

**用途**：包装 model 实现 prompt 增强（注入记忆上下文等）

### 扩展点 3：`ChatAgent.memory` (setter)

```python
agent.memory = CustomAgentMemory(...)  # ← 替换内存系统
```

CAMEL 的 `AgentMemory` 是抽象类，已有 3 种实现：
- `ChatHistoryMemory` — 纯聊天记录
- `VectorDBMemory` — 向量检索
- `LongtermAgentMemory` — 长短期结合

**用途**：注入我们的结构化记忆系统

### 扩展点 4：`generate_agent_graph` 的 profile

```python
agent_graph = await generate_twitter_agent_graph(
    profile_path="custom_profiles.csv",  # ← 自定义人格档案
    model=model,
    available_actions=actions,
)
```

**用途**：增强人格描述，注入认知特征

---

## 方向 B：认知增强插件（最优先）

### 架构

```
oasis-cognition/               # 独立 pip 包
├── __init__.py
├── memory/
│   ├── structured.py          # 结构化记忆（基于 AgentMemory 接口）
│   ├── episodic.py            # 情景记忆
│   └── semantic.py            # 语义记忆
├── cognition/
│   ├── reflection.py          # 反思引擎
│   ├── planning.py            # 规划引擎
│   └── theory_of_mind.py      # 心智理论（理解他人意图）
├── tools/
│   ├── recall.py              # FunctionTool: recall_memory
│   ├── reflect.py             # FunctionTool: reflect
│   └── plan.py                # FunctionTool: make_plan
├── patches/
│   └── agent_patch.py         # monkey-patch perform_action_by_llm
└── integration.py             # 一行接入 OASIS 的入口
```

### 零侵入接入方式

```python
# === 用户代码 ===
import oasis
from oasis_cognition import CognitionPlugin

# 正常创建 OASIS 环境
agent_graph = await generate_twitter_agent_graph(...)
env = oasis.make(agent_graph=agent_graph, platform=...)
await env.reset()

# 一行接入认知增强
plugin = CognitionPlugin(
    memory_type="structured",      # structured | episodic | vector
    enable_reflection=True,        # 每 N 轮自动反思
    enable_planning=False,         # 规划（后续开启）
    enable_tom=False,              # 心智理论（后续开启）
    reflection_interval=3,         # 每 3 轮反思一次
    memory_capacity=50,            # 每个 agent 最多保留 50 条记忆
    memory_half_life=5,            # 时间衰减半衰期（轮次）
)
plugin.attach(env)                 # ← 自动增强所有 agent

# 正常运行
await env.step({agent: LLMAction() for _, agent in env.agent_graph.get_agents()})
```

### `plugin.attach(env)` 内部做了什么

```python
class CognitionPlugin:
    def attach(self, env: OasisEnv):
        for agent_id, agent in env.agent_graph.get_agents():
            # 1. 注入记忆系统（替换 CAMEL 默认的 ChatHistoryMemory）
            agent.memory = StructuredAgentMemory(
                agent_id=agent_id,
                capacity=self.memory_capacity,
                half_life=self.memory_half_life,
            )

            # 2. 注入认知工具（通过 tools 参数）
            cognitive_tools = []
            if self.enable_reflection:
                cognitive_tools.append(FunctionTool(self._make_reflect_tool(agent)))
            agent.external_tools = cognitive_tools  # CAMEL ChatAgent 支持

            # 3. 增强 perform_action_by_llm（monkey-patch）
            original_perform = agent.perform_action_by_llm
            agent.perform_action_by_llm = self._wrap_perform(agent, original_perform)

    def _wrap_perform(self, agent, original_fn):
        async def enhanced_perform():
            # 前置：注入记忆到 prompt
            memories = agent.memory.retrieve_relevant(context=...)
            memory_prompt = self._format_memories(memories)

            # 执行原始 LLM 决策
            result = await original_fn()

            # 后置：记录行为到记忆
            agent.memory.write_record(MemoryRecord(
                type="action",
                content=str(result),
                importance=0.6,
            ))

            # 定期反思
            if self._should_reflect(agent):
                reflection = await self._generate_reflection(agent)
                agent.memory.write_record(MemoryRecord(
                    type="reflection",
                    content=reflection,
                    importance=1.0,
                ))

            return result
        return enhanced_perform
```

### 记忆系统设计（StructuredAgentMemory）

实现 CAMEL 的 `AgentMemory` 接口，这样 OASIS 完全无感知：

```python
class StructuredAgentMemory(AgentMemory):
    """
    实现 CAMEL AgentMemory 接口的结构化记忆。
    """

    def __init__(self, agent_id, capacity=50, half_life=5):
        self.agent_id = agent_id
        self.records: List[StructuredRecord] = []
        self.capacity = capacity
        self.half_life = half_life
        self._context_creator = ...  # 复用 CAMEL 的 context creator

    # --- AgentMemory 接口 ---

    def retrieve(self) -> List[ContextRecord]:
        """CAMEL 调用这个获取上下文"""
        # 1. 时间衰减打分
        # 2. MMR 多样性筛选
        # 3. 转换为 ContextRecord 格式
        return self._select_memories(top_k=8)

    def write_records(self, records: List[MemoryRecord]) -> None:
        """CAMEL 调用这个写入记录"""
        for record in records:
            self._add_record(record)
        self._evict_if_full()

    def get_context_creator(self) -> BaseContextCreator:
        return self._context_creator

    def clear(self) -> None:
        self.records.clear()

    # --- 扩展能力 ---

    def retrieve_relevant(self, context: str, top_k=8) -> List[StructuredRecord]:
        """语义相关性检索（超出 CAMEL 接口的增强能力）"""
        ...

    def get_reflection_candidates(self, n=10) -> List[StructuredRecord]:
        """获取最近 N 条记录用于反思"""
        ...
```

### 记忆记录格式

```python
@dataclass
class StructuredRecord:
    type: str           # action | observation | reflection | social
    content: str        # 自然语言描述
    timestamp: float    # unix timestamp
    importance: float   # 0.0 ~ 1.0
    round: int          # 轮次
    related_agents: List[int] = field(default_factory=list)  # 涉及的其他 agent
    embedding: Optional[List[float]] = None  # 可选，用于语义检索
```

---

## 方向 C：World Kernel（长远目标）

### 思路

同样零侵入，通过包装 `OasisEnv.step()` 实现：

```python
from oasis_kernel import WorldKernel

kernel = WorldKernel(
    rules=[EconomicRule(), WeatherRule(), EventRule()],
    event_bus=EventBus(),
    time_model="continuous",  # continuous | discrete
)
kernel.attach(env)

# env.step() 现在会：
# 1. 执行 kernel.pre_step() — 世界规则更新
# 2. 执行原始 agent actions
# 3. 执行 kernel.post_step() — 因果推理 + 涌现检测
```

**不详细展开，等 B 跑通后再设计。**

---

## 文件结构

```
oasis-cognition/           # 独立 Git 仓库
├── pyproject.toml
├── README.md
├── oasis_cognition/
│   ├── __init__.py
│   ├── plugin.py          # CognitionPlugin 入口
│   ├── memory/
│   ├── cognition/
│   ├── tools/
│   └── patches/
├── tests/
└── examples/
    └── twitter_with_cognition.py
```

---

## 为什么这个方案可行

| 需求 | 方案 |
|------|------|
| 不改 OASIS 代码 | ✅ 通过 `agent.memory` setter + `tools` 注入 + monkey-patch |
| 可插拔 | ✅ plugin.attach() / plugin.detach()，随时开关 |
| 替换其他实现 | ✅ memory_type 参数选择不同记忆后端 |
| 代码质量 | ✅ 独立仓库，独立测试，不污染 OASIS |
| 渐进式 | ✅ 先 memory → 再 reflection → 再 planning → 再 ToM |

---

## 待确认

1. **独立仓库名**：`oasis-cognition`？还是继续在 `worldmind` 里？
2. **第一个 MVP**：只做 StructuredAgentMemory + 记忆注入，不做反思？
3. **测试场景**：用 OASIS 自带的 200 人 Twitter 数据集跑 5 轮对比？
