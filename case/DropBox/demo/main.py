"""
Dropbox Demo (FastAPI) —— 教学版
=================================
对应 ../DropBox.md，覆盖文档里的四个核心需求：上传、下载、共享、同步。

上传走文档 3.1 方案三 + 4.1 分块上传，**文件字节一次都不经过应用服务器**：

    POST /files/initiate         开上传会话，拿到每一块的预签名 URL
    PUT  /chunk/{id}/{no}        模拟 S3，各块直传，失败只重传这一块
    GET  /uploads/{id}           还缺哪些块（断点续传 / 进度条）
    POST /files/complete         合并分块 + 校验 + 写元数据

其余：

    GET  /files                  我的文件 + 共享给我的
    GET  /files/{file_id}        元数据 + 预签名下载 URL（文档 3.2）
    GET  /download/{file_id}     模拟 S3/CDN，下载通行证的落点
    POST /files/{file_id}/share  共享给其他用户       （文档 3.3）
    GET  /changes                拉取变更，用于同步   （文档 3.4）

不用真实数据库和 S3：四张表分别是 data/*.txt，文件内容放 data/blobs/。
实现细节都在 helper.py 里，这个文件只管路由。

启动：  uvicorn main:app --reload
文档：  http://127.0.0.1:8000/docs
"""
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

import helper

app = FastAPI(title="Dropbox Demo", description="txt 当元数据库 + 本地目录当 S3")

helper.init_db()
print(f"[启动] 数据目录 {helper.DATA_DIR}，四张表：files / shares / changes / users")


def current_user(x_user_id: str = Header(default="user1")) -> str:
    """
    用户身份从 header 取（文档 2.2：用户信息走 header，不放在请求体里）。

    真实环境这里解析的是 JWT / session token；demo 直接信任 X-User-Id，
    换个 header 值就等于换个用户登录，方便演示共享。
    """
    helper.user_touch(x_user_id)
    return x_user_id


class InitiateRequest(BaseModel):
    name: str
    content_hash: str                              # 客户端在本地算好的整文件 sha256
    total_chunks: int = 1                          # 切成几块
    chunk_size: int = helper.DEFAULT_CHUNK_SIZE
    mime_type: str = "application/octet-stream"


class CompleteRequest(BaseModel):
    upload_id: str


class ShareRequest(BaseModel):
    users: list[str]


# ===========================================================================
# 上传：分块 + 预签名（文档 3.1 方案三 + 4.1 分块上传）
# ===========================================================================
# 核心思想两条：
#   1. **文件字节一次都不经过应用服务器** —— 服务器只发通行证（方案三）
#   2. **大文件切成块分别传** —— 断点续传、并行上传、进度显示（4.1）
#
#   ① POST /files/initiate        开会话，拿到每一块的通行证
#   ② PUT  /chunk/{id}/{no}  × N  各块并行直传「S3」，失败只重传那一块
#   ③ GET  /uploads/{id}          断线重连后问「还缺哪些块」
#   ④ POST /files/complete        合并 + 校验 + 写元数据
@app.post("/files/initiate", tags=["上传"])
def initiate(req: InitiateRequest, user: str = Depends(current_user)) -> dict:
    """
    第 1 步：开一个上传会话，**每块发一张预签名 URL**。

    客户端先在本地算好整个文件的 sha256 再来，所以服务器可以直接回答
    「这份内容我已经有了」—— 连一个字节都不用传，秒传（文档 4.2.4 去重），
    这时元数据当场就写好，流程结束。真实 Dropbox 上传常见文件那么快就是这个原因。
    """
    if helper.blob_exists(req.content_hash):
        meta = helper.file_put(
            name=req.name,
            size=helper.blob_size(req.content_hash),
            mime_type=req.mime_type,
            content_hash=req.content_hash,
            owner=user,
        )
        return {"blob_exists": True, "file_id": meta["file_id"], "version": meta["version"]}

    upload_id = helper.upload_create(
        name=req.name,
        mime_type=req.mime_type,
        content_hash=req.content_hash,
        chunk_size=req.chunk_size,
        total_chunks=req.total_chunks,
        owner=user,
    )
    # 块多的时候真实系统会分批签发，不会一次返回几千个 URL
    return {
        "blob_exists": False,
        "upload_id": upload_id,
        "chunks": [
            {"chunk_no": n, "upload_url": helper.presign_upload_chunk(upload_id, n)}
            for n in range(1, req.total_chunks + 1)
        ],
    }


@app.put("/chunk/{upload_id}/{chunk_no}", tags=["上传"])
async def upload_chunk(upload_id: str, chunk_no: int, expires: int, sig: str,
                       request: Request) -> dict:
    """
    第 2 步：【这个接口扮演 S3】客户端把这一块的字节直接 PUT 到这里。

    注意是**流式写盘**：`request.stream()` 边收边写，内存里只留一小段缓冲。
    如果写成 `await request.body()`，整块都会先进内存 —— 5MB 的块还行，
    要是有人一次传 50GB 就直接 OOM 了。
    """
    if not helper.presign_valid("PUT", f"{upload_id}/{chunk_no}", expires, sig):
        raise HTTPException(403, "预签名 URL 无效或已过期")
    if not helper.upload_get(upload_id):
        raise HTTPException(404, "上传会话不存在")

    size = 0
    with helper.chunk_open(upload_id, chunk_no) as f:
        async for part in request.stream():
            f.write(part)
            size += len(part)

    helper.chunk_mark(upload_id, chunk_no, size)
    return {"chunk_no": chunk_no, "size": size}


@app.get("/uploads/{upload_id}", tags=["上传"])
def upload_status(upload_id: str, user: str = Depends(current_user)) -> dict:
    """
    第 3 步（可选）：断点续传和进度条的数据来源。

    客户端断线重连后拿 upload_id 问一句「还缺哪些块」，只补传缺的那几块，
    不用从头再来 —— 这就是分块上传最大的价值。
    """
    session = helper.upload_get(upload_id)
    if not session:
        raise HTTPException(404, "上传会话不存在")

    total = int(session["total_chunks"])
    done = helper.chunks_uploaded(upload_id)
    return {
        "upload_id": upload_id,
        "name": session["name"],
        "total_chunks": total,
        "uploaded": sorted(done),
        "missing": [n for n in range(1, total + 1) if n not in done],
        "progress": f"{len(done)}/{total}",
    }


@app.post("/files/complete", tags=["上传"])
def upload_complete(req: CompleteRequest, user: str = Depends(current_user)) -> dict:
    """
    第 4 步：所有块都到了，合并成一个文件，然后写元数据。

    **内容和元数据分两个地方存**，这是这道题最核心的一点：

        文件内容 -> data/blobs/{sha256}   （模拟 S3）
        元数据   -> data/files.txt        （模拟 DynamoDB）

    合并时会核对整文件的 sha256 跟 initiate 时声明的是否一致，
    对不上就拒收 —— 否则客户端可以往通行证里塞任意内容。
    """
    session = helper.upload_get(req.upload_id)
    if not session:
        raise HTTPException(404, "上传会话不存在")
    if session["owner"] != user:
        raise HTTPException(403, "不是这个上传会话的所有者")

    try:
        content_hash = helper.upload_finalize(req.upload_id, session["content_hash"])
    except ValueError as e:
        raise HTTPException(400, str(e))

    helper.upload_cleanup(req.upload_id)          # 合并完丢弃分块，跟 S3 一样

    meta = helper.file_put(
        name=session["name"],
        size=helper.blob_size(content_hash),      # 大小以「S3」里的实际字节为准
        mime_type=session["mime_type"],
        content_hash=content_hash,
        owner=user,
    )
    return {
        "file_id": meta["file_id"],
        "version": meta["version"],               # 同名再上传就是 version + 1
        "size": meta["size"],
        "content_hash": meta["content_hash"],
    }


# ===========================================================================
# 列出 / 查看（文档 3.2、3.3）
# ===========================================================================
@app.get("/files", tags=["核心"])
def list_files(user: str = Depends(current_user)) -> dict:
    """我的文件 + 共享给我的文件"""
    files = helper.files_latest()
    shared = set(helper.shared_with_me(user))
    return {
        "owned": [f for f in files.values() if f["owner"] == user],
        "shared_with_me": [f for fid, f in files.items() if fid in shared],
    }


@app.get("/files/{file_id}", tags=["核心"])
def get_file(file_id: str, user: str = Depends(current_user)) -> dict:
    """
    返回元数据 + 预签名下载 URL（文档 3.2 方案二/三）。

    注意这里**不返回文件内容** —— 内容让客户端拿着预签名 URL 直接从 S3/CDN 取，
    不让 50GB 的大文件穿过应用服务器两次。
    """
    meta = helper.file_get(file_id)
    if not meta:
        raise HTTPException(404, "文件不存在")
    if file_id not in helper.visible_files(user):
        raise HTTPException(403, "没有权限访问这个文件")

    return {"metadata": meta, "download_url": helper.presign_download(file_id)}


# ===========================================================================
# 下载：模拟 S3 / CDN（文档 3.2、4.3.4）
# ===========================================================================
@app.get("/download/{file_id}", tags=["核心"])
def download(file_id: str, expires: int, sig: str) -> Response:
    """
    【这个接口扮演 S3/CDN，不是应用服务器】

    所以它只验签名和过期时间，**完全不看 X-User-Id** —— S3 不认识你的用户系统，
    签名本身就是凭证。权限在上一步 GET /files/{file_id} 就已经检查过了。
    """
    if not helper.presign_valid("GET", file_id, expires, sig):
        raise HTTPException(403, "预签名 URL 无效或已过期")

    meta = helper.file_get(file_id)
    content = helper.blob_read(meta["content_hash"]) if meta else None
    if content is None:
        raise HTTPException(404, "文件内容不存在")

    return Response(
        content,
        media_type=meta["mime_type"],
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(meta['name'])}"},
    )


# ===========================================================================
# 共享（文档 3.3）
# ===========================================================================
@app.post("/files/{file_id}/share", tags=["核心"])
def share(file_id: str, req: ShareRequest, user: str = Depends(current_user)) -> dict:
    """共享关系写进单独的 shares 表，不塞进文件元数据里"""
    meta = helper.file_get(file_id)
    if not meta:
        raise HTTPException(404, "文件不存在")
    if meta["owner"] != user:
        raise HTTPException(403, "只有文件所有者可以共享")

    for target in req.users:
        if not helper.user_exists(target):
            raise HTTPException(404, f"用户不存在: {target}")
        helper.share_add(file_id, target, user)

    return {"file_id": file_id, "shared_with": helper.share_users(file_id)}


# ===========================================================================
# 同步（文档 3.4）
# ===========================================================================
@app.get("/changes", tags=["核心"])
def changes(since: int = 0, user: str = Depends(current_user)) -> dict:
    """
    拉取 since 之后的变更，客户端只下载 version 变大的文件。

    demo 用的是客户端带游标轮询（文档 3.4 方案一）；真实系统是
    变更事件进 Kafka -> 同步服务订阅 -> WebSocket 主动推给用户的其他设备（方案三）。
    """
    visible = helper.visible_files(user)
    rows = [c for c in helper.changes_since(since) if c["file_id"] in visible]
    return {"changes": rows, "cursor": helper.changes_cursor()}
