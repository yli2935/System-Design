'''
Author: Li yli2935@uwo.ca
Date: 2026-01-10 17:57:23
LastEditors: Li
LastEditTime: 2026-01-10 17:58:12
FilePath: /System-Design/core/Networking/SSE-Demo/main.py
'''
# main.py
import asyncio
import json
from typing import Dict, Set

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# 添加 CORS 中间件，允许跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源（生产环境应该指定具体域名）
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有 HTTP 方法
    allow_headers=["*"],  # 允许所有请求头
)

# 每个 user_id 对应一组订阅者队列（一个打开的页面 ≈ 一个队列）
subscribers: Dict[str, Set[asyncio.Queue]] = {}


def sse(data: dict, event: str = "message") -> str:
    # 标准 SSE 格式：event + data + 空行
    return f"event: {event}\n" f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/events/{user_id}")
async def events(user_id: str, request: Request):
    """
    被关注用户的网页：打开时订阅这个 SSE
    """
    q: asyncio.Queue = asyncio.Queue()
    subscribers.setdefault(user_id, set()).add(q)

    async def gen():
        try:
            # 可选：让浏览器断线后 1.5s 自动重连
            yield "retry: 1500\n\n"

            while True:
                # 断开就退出（避免资源泄漏）
                if await request.is_disconnected():
                    break

                # 等待一条要推送的消息
                msg = await q.get()
                yield msg

        finally:
            # 清理订阅
            subscribers.get(user_id, set()).discard(q)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        # Nginx 反代时建议加
        "X-Accel-Buffering": "no",
        # 明确添加 CORS 头
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


@app.post("/follow")
async def follow(follower_id: str, followee_id: str):
    """
    模拟：follower_id 关注 followee_id
    然后给 followee_id 的所有在线页面推送一条通知
    """
    payload = {"type": "follow", "from": follower_id}

    msg = sse(payload, event="notify")

    qs = list(subscribers.get(followee_id, set()))
    for q in qs:
        # 非阻塞放入队列
        q.put_nowait(msg)

    return {"ok": True, "delivered_to_connections": len(qs)}
