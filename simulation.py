"""
OASIS + OpenClaw 完整模拟

轻量级 Platform 实现 + OpenClaw session 驱动的 agent。
不依赖 OASIS Python 代码（它的 Platform 绑死社交媒体），
自己实现社交平台逻辑，通过 sessions_send 驱动 agent 决策。

用法：
    在 OpenClaw 主 session 中执行:
    exec: cd oasis-openclaw && python3 simulation.py
"""

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ============================================================
# 数据模型
# ============================================================

@dataclass
class Post:
    id: int
    author_id: int
    content: str
    likes: List[int] = field(default_factory=list)
    comments: List['Comment'] = field(default_factory=list)
    reposts: List[int] = field(default_factory=list)
    created_at: int = 0  # round number


@dataclass
class Comment:
    id: int
    post_id: int
    author_id: int
    content: str
    created_at: int = 0


@dataclass
class AgentConfig:
    id: int
    name: str
    personality: str
    background: str = ""
    following: List[int] = field(default_factory=list)
    followers: List[int] = field(default_factory=list)


# ============================================================
# 轻量 Platform
# ============================================================

class SocialPlatform:
    """轻量级社交平台，管理帖子/评论/关注状态"""

    def __init__(self):
        self.posts: Dict[int, Post] = {}
        self.agents: Dict[int, AgentConfig] = {}
        self._next_post_id = 1
        self._next_comment_id = 1
        self.action_log: List[dict] = []

    def register_agent(self, agent: AgentConfig):
        self.agents[agent.id] = agent

    def execute_action(self, agent_id: int, action: dict, round_num: int) -> str:
        """执行 agent 的 action，返回结果描述"""
        action_name = action.get("action", "do_nothing")
        args = action.get("args", {})

        if action_name == "create_post":
            return self._create_post(agent_id, args.get("content", ""), round_num)
        elif action_name == "like_post":
            return self._like_post(agent_id, int(args.get("post_id", 0)))
        elif action_name == "create_comment":
            return self._create_comment(
                agent_id, int(args.get("post_id", 0)),
                args.get("content", ""), round_num
            )
        elif action_name == "repost":
            return self._repost(agent_id, int(args.get("post_id", 0)))
        elif action_name == "follow":
            return self._follow(agent_id, int(args.get("user_id", 0)))
        elif action_name == "do_nothing":
            self.action_log.append({
                "round": round_num, "agent": agent_id,
                "action": "do_nothing"
            })
            return "选择不做任何事"
        else:
            return f"未知动作: {action_name}"

    def _create_post(self, agent_id: int, content: str, round_num: int) -> str:
        post = Post(
            id=self._next_post_id, author_id=agent_id,
            content=content, created_at=round_num
        )
        self.posts[post.id] = post
        self._next_post_id += 1
        self.action_log.append({
            "round": round_num, "agent": agent_id,
            "action": "create_post", "post_id": post.id,
            "content": content
        })
        return f"发布帖子 #{post.id}: {content[:50]}"

    def _like_post(self, agent_id: int, post_id: int) -> str:
        post = self.posts.get(post_id)
        if not post:
            return f"帖子 #{post_id} 不存在"
        if agent_id not in post.likes:
            post.likes.append(agent_id)
        self.action_log.append({
            "round": 0, "agent": agent_id,
            "action": "like", "post_id": post_id
        })
        return f"点赞帖子 #{post_id}"

    def _create_comment(self, agent_id: int, post_id: int,
                        content: str, round_num: int) -> str:
        post = self.posts.get(post_id)
        if not post:
            return f"帖子 #{post_id} 不存在"
        comment = Comment(
            id=self._next_comment_id, post_id=post_id,
            author_id=agent_id, content=content, created_at=round_num
        )
        post.comments.append(comment)
        self._next_comment_id += 1
        self.action_log.append({
            "round": round_num, "agent": agent_id,
            "action": "comment", "post_id": post_id,
            "content": content
        })
        return f"评论帖子 #{post_id}: {content[:50]}"

    def _repost(self, agent_id: int, post_id: int) -> str:
        post = self.posts.get(post_id)
        if not post:
            return f"帖子 #{post_id} 不存在"
        if agent_id not in post.reposts:
            post.reposts.append(agent_id)
        self.action_log.append({
            "round": 0, "agent": agent_id,
            "action": "repost", "post_id": post_id
        })
        return f"转发帖子 #{post_id}"

    def _follow(self, agent_id: int, target_id: int) -> str:
        target = self.agents.get(target_id)
        if not target:
            return f"用户 #{target_id} 不存在"
        if target_id not in self.agents[agent_id].following:
            self.agents[agent_id].following.append(target_id)
        if agent_id not in target.followers:
            target.followers.append(agent_id)
        self.action_log.append({
            "round": 0, "agent": agent_id,
            "action": "follow", "target": target_id
        })
        return f"关注了 {target.name}"

    def get_feed(self, agent_id: int, max_posts: int = 5) -> str:
        """生成 agent 看到的 feed"""
        agent = self.agents[agent_id]

        # 推荐：关注者的帖子 + 热门帖子
        relevant_posts = []
        for post in sorted(self.posts.values(),
                           key=lambda p: len(p.likes) + len(p.comments),
                           reverse=True):
            if post.author_id != agent_id:
                relevant_posts.append(post)
            if len(relevant_posts) >= max_posts:
                break

        if not relevant_posts:
            return "当前没有新帖子。"

        lines = ["推荐帖子："]
        for i, post in enumerate(relevant_posts, 1):
            author = self.agents.get(post.author_id)
            author_name = author.name if author else f"用户#{post.author_id}"
            lines.append(
                f"{i}. [帖子#{post.id}] {author_name}: "
                f"\"{post.content}\" "
                f"(点赞:{len(post.likes)}, 评论:{len(post.comments)})"
            )
            # 显示最近的评论
            for comment in post.comments[-2:]:
                commenter = self.agents.get(comment.author_id)
                commenter_name = commenter.name if commenter else f"用户#{comment.author_id}"
                lines.append(f"   💬 {commenter_name}: \"{comment.content}\"")

        # 社交关系
        following_names = [self.agents[fid].name for fid in agent.following
                          if fid in self.agents]
        follower_names = [self.agents[fid].name for fid in agent.followers
                          if fid in self.agents]

        lines.append(f"\n你关注的人：{', '.join(following_names) or '无'}")
        lines.append(f"你的粉丝：{', '.join(follower_names) or '无'}")

        return "\n".join(lines)

    def get_summary(self) -> str:
        """生成平台状态摘要"""
        lines = [
            f"\n{'='*60}",
            f"平台状态：{len(self.posts)} 帖子, "
            f"{sum(len(p.comments) for p in self.posts.values())} 评论, "
            f"{sum(len(p.likes) for p in self.posts.values())} 点赞",
            f"{'='*60}",
        ]
        for post in self.posts.values():
            author = self.agents.get(post.author_id)
            author_name = author.name if author else f"#{post.author_id}"
            lines.append(
                f"  帖子#{post.id} by {author_name}: "
                f"\"{post.content[:60]}\" "
                f"[❤️{len(post.likes)} 💬{len(post.comments)}]"
            )
            for comment in post.comments:
                commenter = self.agents.get(comment.author_id)
                cn = commenter.name if commenter else f"#{comment.author_id}"
                lines.append(f"    └─ {cn}: \"{comment.content[:50]}\"")

        return "\n".join(lines)


# ============================================================
# 输出（写文件给主 agent 读取）
# ============================================================

def write_output(text: str):
    """输出到 stdout 和日志文件"""
    print(text)
    with open("simulation_log.txt", "a", encoding="utf-8") as f:
        f.write(text + "\n")


# ============================================================
# 主流程（由主 agent 执行）
# ============================================================

def build_prompt(platform: SocialPlatform, agent_id: int) -> str:
    """构造发给 OpenClaw session 的完整 prompt"""
    feed = platform.get_feed(agent_id)
    return (
        f"你现在看到的社交平台环境：\n\n"
        f"{feed}\n\n"
        f"请根据你的性格决定下一步动作。只回复JSON："
        '{"action": "动作名", "args": {参数}}'
    )


def parse_action(reply: str) -> Optional[dict]:
    """解析 agent 回复的 action JSON"""
    import re

    # 直接解析
    try:
        return json.loads(reply)
    except json.JSONDecodeError:
        pass

    # 从文本中提取
    match = re.search(
        r'\{[^{}]*"action"\s*:\s*"[^"]*"\s*,\s*"args"\s*:\s*\{[^}]*\}[^}]*\}',
        reply
    )
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # code block
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', reply, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    return None


# ============================================================
# 配置
# ============================================================

AGENTS = [
    AgentConfig(
        id=0, name="小明",
        personality="开朗外向，热爱美食，经常分享吃的照片和餐厅推荐。说话热情洋溢，爱用emoji。",
        background="90后上海白领，周末必探店",
    ),
    AgentConfig(
        id=1, name="小红",
        personality="安静内向，喜欢阅读和独处。偶尔分享书评，言语简洁有深度。",
        background="文学硕士，在出版社工作",
    ),
    AgentConfig(
        id=2, name="大壮",
        personality="热情豪爽，运动达人，篮球和健身狂热爱好者。说话直接，爱开玩笑。",
        background="健身教练，业余篮球队队长",
    ),
]

# 初始关注关系
AGENTS[0].following = [1, 2]  # 小明关注小红和大壮
AGENTS[1].following = [0]      # 小红关注小明
AGENTS[2].following = [0, 1]   # 大壮关注小明和小红

# 种子帖子
SEED_POSTS = [
    Post(id=100, author_id=99, content="上海今天35度，热化了☀️",
         likes=[0, 1], created_at=0),
]


if __name__ == "__main__":
    # 这个脚本输出指令文件，由主 agent 读取并执行 sessions_send
    # 因为 sessions_send 只能从 agent session 内部调用

    platform = SocialPlatform()

    # 注册 agents
    for agent in AGENTS:
        platform.register_agent(agent)

    # 添加种子帖子
    for post in SEED_POSTS:
        platform.posts[post.id] = post
        platform._next_post_id = max(platform._next_post_id, post.id + 1)

    # 生成每轮的 prompt
    NUM_ROUNDS = 3
    instructions = {
        "agents": [
            {
                "id": a.id,
                "name": a.name,
                "label": f"oasis-{a.name}",
                "soul": (
                    f"你是社交平台用户\"{a.name}\"。\n"
                    f"性格：{a.personality}\n"
                    f"背景：{a.background}\n\n"
                    f"规则：收到环境信息后，只用JSON回复："
                    f'{{\"action\": \"动作名\", \"args\": {{参数}}}}\n'
                    f"可用动作：create_post(content), like_post(post_id), "
                    f"create_comment(post_id, content), follow(user_id), do_nothing()"
                ),
            }
            for a in AGENTS
        ],
        "rounds": [],
    }

    for round_num in range(1, NUM_ROUNDS + 1):
        round_data = {"round": round_num, "prompts": []}
        for agent in AGENTS:
            prompt = build_prompt(platform, agent.id)
            round_data["prompts"].append({
                "agent_id": agent.id,
                "label": f"oasis-{agent.name}",
                "prompt": prompt,
            })
        instructions["rounds"].append(round_data)

    # 输出指令文件
    with open("simulation_instructions.json", "w", encoding="utf-8") as f:
        json.dump(instructions, f, ensure_ascii=False, indent=2)

    print("✅ 指令文件已生成: simulation_instructions.json")
    print(f"   {len(AGENTS)} 个 agent, {NUM_ROUNDS} 轮")
    print(f"   种子帖子: {len(SEED_POSTS)} 条")
    print("\n接下来由主 agent 执行：")
    print("1. 读取 simulation_instructions.json")
    print("2. 为每个 agent 调用 sessions_spawn")
    print("3. 每轮调用 sessions_send 发送 prompt")
    print("4. 收集 action 并更新平台状态")
