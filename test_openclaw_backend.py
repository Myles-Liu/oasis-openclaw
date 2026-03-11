"""
测试 OpenClaw Backend 与 OASIS 集成。

先用简单的方式验证核心流程：
1. 能否为每个 agent 创建独立的 OpenClaw session
2. 能否发送环境 prompt 并获取决策
3. 能否解析 action 并执行

运行：
    cd worldmind
    python scripts/test_openclaw_backend.py
"""

import asyncio
import json
import logging
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
logger = logging.getLogger("test")


async def test_session_lifecycle():
    """测试 session 创建和通信"""

    logger.info("=== Test 1: Session 创建 ===")

    # 用 openclaw CLI 创建 session
    proc = await asyncio.create_subprocess_exec(
        "openclaw", "send",
        "--spawn",
        "--label", "test-oasis-agent-1",
        "你是小明，性格开朗。请用一句话介绍自己。只回复角色内容。",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

    logger.info(f"stdout: {stdout.decode()[:500]}")
    if stderr:
        logger.info(f"stderr: {stderr.decode()[:500]}")

    logger.info("=== Test 1 完成 ===\n")


async def test_action_parsing():
    """测试 action JSON 解析"""
    from openclaw_backend import OpenClawBackend

    backend = OpenClawBackend()

    test_cases = [
        '{"action": "create_post", "args": {"content": "今天天气真好！"}}',
        '我决定发一条帖子。\n```json\n{"action": "create_post", "args": {"content": "分享美食"}}\n```',
        '让我想想...我觉得这个帖子很有趣。\n{"action": "like_post", "args": {"post_id": 42}}',
        '没什么感兴趣的。{"action": "do_nothing", "args": {}}',
    ]

    logger.info("=== Test 2: Action 解析 ===")
    for i, text in enumerate(test_cases):
        result = backend._parse_action(text)
        logger.info(f"Case {i+1}: {result}")
    logger.info("=== Test 2 完成 ===\n")


async def test_env_prompt_format():
    """测试构造给 OpenClaw 的环境 prompt"""

    logger.info("=== Test 3: 环境 Prompt 格式 ===")

    # 模拟 OASIS 的环境 prompt
    env_prompt = """
Here are some posts for you:

Post (id=101) by Alice: "刚做了一顿红烧肉，味道绝了！🍖"
    Likes: 5 | Comments: 2 | Reposts: 1

Post (id=102) by Bob: "周末有人一起打篮球吗？"
    Likes: 3 | Comments: 1 | Reposts: 0

Post (id=103) by Carol: "推荐一本好书《人类简史》，值得一读。"
    Likes: 8 | Comments: 5 | Reposts: 3

Your followers: Alice, David
You are following: Alice, Bob, Carol
"""

    message = (
        f"以下是你当前看到的社交平台环境：\n\n"
        f"{env_prompt}\n\n"
        f"请根据你的性格决定下一步动作。"
        f'用 JSON 格式回复：{{"action": "动作名", "args": {{参数}}}}'
    )

    logger.info(f"Prompt length: {len(message)} chars")
    logger.info(f"Prompt preview:\n{message[:300]}...")
    logger.info("=== Test 3 完成 ===\n")


async def test_concurrent_sessions():
    """测试并发创建多个 session"""

    logger.info("=== Test 4: 并发 Session ===")

    profiles = [
        ("小明", "开朗外向，爱分享美食，经常发吃的照片"),
        ("小红", "安静内向，喜欢读书，偶尔分享书评"),
        ("大壮", "热情豪爽，喜欢运动，篮球迷"),
    ]

    async def create_agent(name, personality):
        task = f"你是{name}，性格：{personality}。你是一个社交平台用户。请用一句话描述你的性格。只回复角色内容。"

        proc = await asyncio.create_subprocess_exec(
            "openclaw", "send",
            "--spawn",
            "--label", f"test-concurrent-{name}",
            task,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        reply = stdout.decode().strip()
        logger.info(f"  {name}: {reply[:100]}")
        return reply

    tasks = [create_agent(name, personality) for name, personality in profiles]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success = sum(1 for r in results if isinstance(r, str) and r)
    logger.info(f"成功: {success}/{len(profiles)}")
    logger.info("=== Test 4 完成 ===\n")


async def main():
    logger.info("开始测试 OpenClaw Backend\n")

    # Test 2: Action 解析（纯本地，不需要 OpenClaw）
    await test_action_parsing()

    # Test 3: Prompt 格式（纯本地）
    await test_env_prompt_format()

    # Test 1: Session 创建（需要 OpenClaw Gateway）
    try:
        await test_session_lifecycle()
    except Exception as e:
        logger.error(f"Test 1 failed: {e}")

    # Test 4: 并发 Session（需要 OpenClaw Gateway）
    try:
        await test_concurrent_sessions()
    except Exception as e:
        logger.error(f"Test 4 failed: {e}")

    logger.info("\n所有测试完成！")


if __name__ == "__main__":
    asyncio.run(main())
