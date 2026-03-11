#!/usr/bin/env python3
"""
自动化模拟编排器

生成每轮的 prompt，由主 agent 通过 sessions_spawn/sessions_send 执行。
输出结构化的 JSONL 指令文件供主 agent 读取。

用法：
    python3 run_simulation.py --rounds 5 --agents profiles.json
"""

import json
import sys
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Post:
    id: int
    author_id: int
    content: str
    likes: List[int] = field(default_factory=list)
    comments: list = field(default_factory=list)
    created_round: int = 0

@dataclass
class Comment:
    author_id: int
    content: str
    round: int = 0


class Platform:
    def __init__(self):
        self.posts: Dict[int, Post] = {}
        self.agents: Dict[int, dict] = {}
        self._next_id = 1
        self.round_log: List[dict] = []

    def add_agent(self, profile: dict):
        self.agents[profile["id"]] = profile
        # 互相设置 followers
        for fid in profile.get("following", []):
            if fid in self.agents:
                followers = self.agents[fid].setdefault("followers", [])
                if profile["id"] not in followers:
                    followers.append(profile["id"])

    def seed_post(self, author_name: str, content: str):
        pid = self._next_id
        self._next_id += 1
        self.posts[pid] = Post(id=pid, author_id=-1, content=content)
        self.posts[pid]._author_name = author_name
        return pid

    def execute(self, agent_id: int, action: dict, round_num: int) -> str:
        name = action.get("action", "do_nothing")
        args = action.get("args", {})
        log = {"round": round_num, "agent_id": agent_id,
               "agent_name": self.agents[agent_id]["name"],
               "action": name}

        if name == "create_post":
            pid = self._next_id
            self._next_id += 1
            content = args.get("content", "")
            self.posts[pid] = Post(id=pid, author_id=agent_id,
                                   content=content, created_round=round_num)
            log["post_id"] = pid
            log["content"] = content
            self.round_log.append(log)
            return f"发帖 #{pid}"

        elif name == "like_post":
            pid = int(args.get("post_id", 0))
            if pid in self.posts and agent_id not in self.posts[pid].likes:
                self.posts[pid].likes.append(agent_id)
            log["post_id"] = pid
            self.round_log.append(log)
            return f"点赞 #{pid}"

        elif name == "create_comment":
            pid = int(args.get("post_id", 0))
            content = args.get("content", "")
            if pid in self.posts:
                self.posts[pid].comments.append(
                    Comment(author_id=agent_id, content=content, round=round_num)
                )
            log["post_id"] = pid
            log["content"] = content
            self.round_log.append(log)
            return f"评论 #{pid}"

        elif name == "follow":
            log["target"] = args.get("user_id")
            self.round_log.append(log)
            return f"关注 #{args.get('user_id')}"

        else:
            log["action"] = "do_nothing"
            self.round_log.append(log)
            return "do_nothing"

    def get_feed(self, agent_id: int, max_posts: int = 5) -> str:
        agent = self.agents[agent_id]
        following = set(agent.get("following", []))

        # 排序：关注的人的帖子优先，然后按热度
        def score(p):
            is_following = 1 if p.author_id in following else 0
            return (is_following, len(p.likes) + len(p.comments))

        posts = [p for p in self.posts.values() if p.author_id != agent_id]
        posts.sort(key=score, reverse=True)
        posts = posts[:max_posts]

        if not posts:
            return "当前没有新帖子。你可以发一条帖子。"

        lines = ["推荐帖子："]
        for i, p in enumerate(posts, 1):
            if hasattr(p, '_author_name'):
                aname = p._author_name
            elif p.author_id in self.agents:
                aname = self.agents[p.author_id]["name"]
            else:
                aname = f"用户#{p.author_id}"

            lines.append(
                f"{i}. [帖子#{p.id}] {aname}: "
                f"\"{p.content}\" "
                f"(❤️{len(p.likes)} 💬{len(p.comments)})"
            )
            for c in p.comments[-3:]:  # 最近3条评论
                if c.author_id in self.agents:
                    cn = self.agents[c.author_id]["name"]
                elif c.author_id == agent_id:
                    cn = f"{agent['name']}(你)"
                else:
                    cn = f"用户#{c.author_id}"
                lines.append(f"   └─ {cn}: \"{c.content[:80]}\"")

        following_names = [self.agents[f]["name"] for f in agent.get("following", [])
                          if f in self.agents]
        follower_ids = [aid for aid, a in self.agents.items()
                        if agent_id in a.get("following", [])]
        follower_names = [self.agents[f]["name"] for f in follower_ids
                          if f in self.agents]

        lines.append(f"\n你关注的人：{', '.join(following_names) or '无'}")
        lines.append(f"你的粉丝：{', '.join(follower_names) or '无'}")

        return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--agents", default="profiles.json")
    parser.add_argument("--output", default="sim_commands.jsonl")
    args = parser.parse_args()

    with open(args.agents, "r") as f:
        profiles = json.load(f)

    platform = Platform()
    for p in profiles:
        p.setdefault("followers", [])
        platform.add_agent(p)

    # 种子帖子
    platform.seed_post("热搜", "上海今天35度，热化了☀️")
    platform.seed_post("热搜", "某大厂宣布全员降薪15%，互联网寒冬继续")
    platform.seed_post("热搜", "《哈利波特》新电视剧预告片发布，你期待吗？")

    commands = []

    # Phase 1: spawn 所有 agent session
    for p in profiles:
        soul = (
            f"你是社交平台用户\"{p['name']}\"。\n"
            f"性格：{p['personality']}\n"
            f"背景：{p['background']}\n\n"
            f"规则：\n"
            f"1. 收到环境信息后，只用JSON回复\n"
            f"2. 格式：{{\"action\": \"动作名\", \"args\": {{参数}}}}\n"
            f"3. 可用动作：create_post(content), like_post(post_id), "
            f"create_comment(post_id, content), follow(user_id), do_nothing()\n"
            f"4. 内容要符合你的性格，用中文\n"
            f"5. 不要回复任何解释，只回复JSON"
        )
        commands.append({
            "type": "spawn",
            "agent_id": p["id"],
            "label": f"sim-{p['id']}-{p['name']}",
            "soul": soul,
        })

    # Phase 2: 每轮生成 prompt 并收集结果
    for round_num in range(1, args.rounds + 1):
        round_cmds = []
        for p in profiles:
            feed = platform.get_feed(p["id"])
            prompt = (
                f"[第{round_num}轮] 你现在看到的社交平台环境：\n\n"
                f"{feed}\n\n"
                f"请根据你的性格决定下一步动作。只回复JSON。"
            )
            round_cmds.append({
                "type": "send",
                "round": round_num,
                "agent_id": p["id"],
                "label": f"sim-{p['id']}-{p['name']}",
                "prompt": prompt,
            })

        commands.extend(round_cmds)

        # 生成占位 — 实际执行时需要根据回复更新 platform
        commands.append({
            "type": "round_end",
            "round": round_num,
            "note": "收集本轮所有回复后，更新 platform 状态再进入下一轮",
        })

    # 写出
    with open(args.output, "w", encoding="utf-8") as f:
        for cmd in commands:
            f.write(json.dumps(cmd, ensure_ascii=False) + "\n")

    print(f"✅ 生成 {len(commands)} 条指令 → {args.output}")
    print(f"   {len(profiles)} agents × {args.rounds} rounds")
    print(f"   种子帖子: {len(platform.posts)}")
    print(f"\n指令类型统计:")
    from collections import Counter
    types = Counter(c["type"] for c in commands)
    for t, n in types.items():
        print(f"   {t}: {n}")


if __name__ == "__main__":
    main()
