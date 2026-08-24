"""
辅助函数 —— Dropbox demo 的基础能力
====================================
main.py 只管路由，「怎么存、怎么签名」都在这里：

    1. txt 当数据库表   table_read() / table_append()             <- 一个 txt 一张表
    2. Blob 存储        blob_read() / blob_exists()               <- 模拟 S3，文档 3.1
    3. 预签名 URL       presign_upload_chunk() / presign_download() <- 文档 3.1 方案三、4.3.4
    4. 六张表的读写      file_* / share_* / change_* / user_*
    5. 分块上传          upload_* / chunk_*                        <- 文档 4.1

关键设计：**元数据和文件内容分开存**（文档 3.1）
    元数据 -> data/*.txt      结构化、要查询、体积小   （真实环境：DynamoDB）
    内容   -> data/blobs/     非结构化、只按 key 取     （真实环境：S3）
"""
import hashlib
import hmac
import secrets
import shutil
import time
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
BLOB_DIR = DATA_DIR / "blobs"           # 模拟 S3：只放完整的文件内容
CHUNK_DIR = DATA_DIR / "chunks"         # 分块暂存区（模拟 S3 multipart 的 parts），合并后删掉

# ---- 六张“表”，一个 txt 一张 ----------------------------------------------
FILES = DATA_DIR / "files.txt"          # 文件元数据表（模拟 DynamoDB）
SHARES = DATA_DIR / "shares.txt"        # 共享关系表（文档 3.3 方案二）
CHANGES = DATA_DIR / "changes.txt"      # 变更日志表（同步用，文档 3.4）
USERS = DATA_DIR / "users.txt"          # 用户表
UPLOADS = DATA_DIR / "uploads.txt"      # 上传会话表（分块上传用，文档 4.1）
CHUNKS = DATA_DIR / "chunks.txt"        # 已到达的分块（断点续传靠它）

# 每张表的字段，相当于 CREATE TABLE，也会写进 txt 第一行当表头
SCHEMAS = {
    FILES: ["file_id", "name", "size", "mime_type", "content_hash", "owner", "version", "updated_at"],
    SHARES: ["file_id", "shared_with", "shared_by", "shared_at"],
    CHANGES: ["seq", "file_id", "owner", "version", "event", "updated_at"],
    USERS: ["user_id", "created_at"],
    UPLOADS: ["upload_id", "name", "mime_type", "content_hash", "chunk_size", "total_chunks", "owner", "created_at"],
    CHUNKS: ["upload_id", "chunk_no", "size", "uploaded_at"],
}

BASE_URL = "http://127.0.0.1:8000"
SECRET = b"demo-secret-key"             # 真实环境是云厂商密钥，绝不能进代码库
PRESIGN_TTL = 300                       # 预签名 URL 有效期 5 分钟
DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024    # 5MB —— S3 multipart 规定的最小块大小


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ===========================================================================
# 1. txt 当数据库表
# ===========================================================================
def init_db() -> None:
    """建库建表：确保目录和六个 txt 都存在，并写好表头"""
    BLOB_DIR.mkdir(parents=True, exist_ok=True)
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    for table, cols in SCHEMAS.items():
        if not table.exists():
            table.write_text("# " + "\t".join(cols) + "\n", encoding="utf-8")


def table_read(table: Path) -> list[dict]:
    """全表扫描：一行一条记录，制表符分隔，`#` 开头的是表头/注释"""
    if not table.exists():
        return []
    cols = SCHEMAS[table]
    return [
        dict(zip(cols, line.split("\t")))
        for line in table.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def table_append(table: Path, row: dict) -> None:
    """插入一条记录：txt 末尾追加一行（不重写整个文件）"""
    cols = SCHEMAS[table]
    # 制表符是列分隔符，值里出现了就得换掉，否则会把行结构冲坏
    values = [str(row[c]).replace("\t", " ").replace("\n", " ") for c in cols]
    with table.open("a", encoding="utf-8") as f:
        f.write("\t".join(values) + "\n")


# ===========================================================================
# 2. Blob 存储（模拟 S3，文档 3.1）
# ===========================================================================
# blob 的文件名就是内容的 sha256，所以相同内容天然只存一份（文档 4.2.4 重复数据删除）：
# 一百个人上传同一个文件，blobs/ 里也只有一个 blob。
def blob_exists(content_hash: str) -> bool:
    return (BLOB_DIR / content_hash).exists()


def blob_read(content_hash: str) -> bytes | None:
    blob = BLOB_DIR / content_hash
    return blob.read_bytes() if blob.exists() else None


def blob_size(content_hash: str) -> int:
    return (BLOB_DIR / content_hash).stat().st_size


# ===========================================================================
# 3. 预签名 URL（文档 3.1 方案三、3.2 方案二、4.3.4）
# ===========================================================================
def _sign(method: str, key: str, expires: int) -> str:
    """用密钥对 (HTTP 方法, 资源 key, 过期时间) 签名。没有密钥就伪造不出来。

    签名里带上 method，是为了让下载用的 URL 不能拿去上传 —— 真实的 S3 预签名
    也是把方法、路径、过期时间一起签进去的。
    """
    return hmac.new(SECRET, f"{method}:{key}:{expires}".encode(), hashlib.sha256).hexdigest()[:16]


def presign_upload_chunk(upload_id: str, chunk_no: int) -> str:
    """上传通行证：**一块一张**。客户端拿它把这一块的字节直接 PUT 到「S3」"""
    expires = int(time.time()) + PRESIGN_TTL
    key = f"{upload_id}/{chunk_no}"
    return f"{BASE_URL}/chunk/{key}?expires={expires}&sig={_sign('PUT', key, expires)}"


def presign_download(file_id: str) -> str:
    """下载通行证：客户端拿它直接去「S3/CDN」取文件"""
    expires = int(time.time()) + PRESIGN_TTL
    return f"{BASE_URL}/download/{file_id}?expires={expires}&sig={_sign('GET', file_id, expires)}"


def presign_valid(method: str, key: str, expires: int, sig: str) -> bool:
    """验签 + 查过期。这一步不查数据库，也不需要知道你是谁 —— 签名本身就是凭证"""
    if expires < time.time():
        return False
    return hmac.compare_digest(sig, _sign(method, key, expires))


# ===========================================================================
# 4-1. files 表：文件元数据
# ===========================================================================
def make_file_id(owner: str, name: str) -> str:
    """同一用户的同名文件视为同一个文件，再上传就是新版本"""
    return hashlib.md5(f"{owner}:{name}".encode()).hexdigest()[:12]


def files_latest() -> dict[str, dict]:
    """files.txt 是追加写的版本日志，读的时候按 file_id 取 version 最大的那条"""
    latest: dict[str, dict] = {}
    for row in table_read(FILES):
        cur = latest.get(row["file_id"])
        if cur is None or int(row["version"]) > int(cur["version"]):
            latest[row["file_id"]] = row
    return latest


def file_get(file_id: str) -> dict | None:
    return files_latest().get(file_id)


def file_put(name: str, size: int, mime_type: str, content_hash: str, owner: str) -> dict:
    """写一条元数据。同名再上传 => version + 1（文档 3.4 用版本号追踪更新）"""
    file_id = make_file_id(owner, name)
    old = file_get(file_id)
    row = {
        "file_id": file_id,
        "name": name,
        "size": size,
        "mime_type": mime_type,
        "content_hash": content_hash,
        "owner": owner,
        "version": int(old["version"]) + 1 if old else 1,
        "updated_at": now(),
    }
    table_append(FILES, row)
    change_log(row, "updated" if old else "created")
    return row


# ===========================================================================
# 4-2. shares 表：共享关系（文档 3.3 方案二）
# ===========================================================================
# 单独一张表，而不是在元数据里塞一个 sharedWith 数组。
# 好处：(file_id, shared_with) 两个方向都能查 —— 既能查「这个文件共享给了谁」，
#      也能查「哪些文件共享给了我」，不用扫全表比对数组。
def share_add(file_id: str, shared_with: str, shared_by: str) -> None:
    if shared_with in share_users(file_id):
        return          # 已经共享过，不重复插入
    table_append(SHARES, {
        "file_id": file_id,
        "shared_with": shared_with,
        "shared_by": shared_by,
        "shared_at": now(),
    })


def share_users(file_id: str) -> list[str]:
    """这个文件共享给了谁"""
    return [r["shared_with"] for r in table_read(SHARES) if r["file_id"] == file_id]


def shared_with_me(user: str) -> list[str]:
    """哪些文件共享给了我"""
    return [r["file_id"] for r in table_read(SHARES) if r["shared_with"] == user]


def visible_files(user: str) -> set[str]:
    """我能看到的文件 = 我上传的 + 共享给我的"""
    owned = {fid for fid, f in files_latest().items() if f["owner"] == user}
    return owned | set(shared_with_me(user))


# ===========================================================================
# 4-3. changes 表：变更日志（文档 3.4 同步）
# ===========================================================================
# demo 用「客户端带 cursor 轮询」这种最简单的方式。
# 真实系统：变更事件发到 Kafka -> 同步服务订阅 -> WebSocket 主动推给其他设备。
def change_log(file_row: dict, event: str) -> None:
    table_append(CHANGES, {
        "seq": len(table_read(CHANGES)) + 1,        # 自增游标
        "file_id": file_row["file_id"],
        "owner": file_row["owner"],
        "version": file_row["version"],
        "event": event,
        "updated_at": file_row["updated_at"],
    })


def changes_since(seq: int) -> list[dict]:
    """拉取 seq 之后的变更，客户端只需同步这些文件"""
    return [r for r in table_read(CHANGES) if int(r["seq"]) > seq]


def changes_cursor() -> int:
    return len(table_read(CHANGES))


# ===========================================================================
# 4-4. users 表
# ===========================================================================
def user_touch(user_id: str) -> None:
    """demo 简化：第一次出现的用户自动注册（真实环境是独立的注册/登录流程）"""
    if not user_exists(user_id):
        table_append(USERS, {"user_id": user_id, "created_at": now()})


def user_exists(user_id: str) -> bool:
    return any(r["user_id"] == user_id for r in table_read(USERS))


# ===========================================================================
# 5. 分块上传（文档 4.1）
# ===========================================================================
# 为什么大文件不能一次传完：50GB 单次 PUT 要传一个多小时，中途断线就得从 0 重来，
# 而且没法显示进度、没法并行。切成 5MB 的小块就解决了这四个问题：
#     断点续传 —— 失败只重传那一块
#     并行上传 —— 多块同时传，吃满带宽
#     进度显示 —— 已到达块数 / 总块数
# 真实环境用 S3 Multipart Upload，S3 负责合并（最小 5MB，最多 10000 块）。
#
# uploads 表是上传会话（谁在传什么，应用服务器的账本）；
# chunks 表是哪些块已经到了 —— 严格说这属于「S3」那边的账本
# （真实环境是 S3 的 ListParts 接口），demo 里为了简单放一起了。
def upload_create(name: str, mime_type: str, content_hash: str,
                  chunk_size: int, total_chunks: int, owner: str) -> str:
    """开一个上传会话，返回 upload_id（对应 S3 的 CreateMultipartUpload）"""
    upload_id = secrets.token_hex(6)
    table_append(UPLOADS, {
        "upload_id": upload_id,
        "name": name,
        "mime_type": mime_type,
        "content_hash": content_hash,       # 客户端声明的整文件 sha256，合并后要核对
        "chunk_size": chunk_size,
        "total_chunks": total_chunks,
        "owner": owner,
        "created_at": now(),
    })
    return upload_id


def upload_get(upload_id: str) -> dict | None:
    return next((r for r in table_read(UPLOADS) if r["upload_id"] == upload_id), None)


def chunk_open(upload_id: str, chunk_no: int):
    """打开分块暂存文件准备写入。调用方**流式**往里写，不要先攒到内存里"""
    path = CHUNK_DIR / upload_id / str(chunk_no)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("wb")


def chunk_mark(upload_id: str, chunk_no: int, size: int) -> None:
    """记一笔「这块到了」。重传同一块会再追加一行，读的时候按集合去重"""
    table_append(CHUNKS, {
        "upload_id": upload_id,
        "chunk_no": chunk_no,
        "size": size,
        "uploaded_at": now(),
    })


def chunks_uploaded(upload_id: str) -> set[int]:
    """已经到达的块号 —— 断点续传和进度条都靠它"""
    return {int(r["chunk_no"]) for r in table_read(CHUNKS) if r["upload_id"] == upload_id}


def upload_finalize(upload_id: str, expected_hash: str) -> str:
    """按块号顺序合并成一个 blob，返回实际的 content_hash。

    一次只读一块进内存（默认 5MB），所以 50GB 的文件也撑不爆内存 —— 真实 S3 同理。
    分块不全或合并结果跟客户端声明的 hash 不符，都会抛 ValueError。
    """
    session = upload_get(upload_id)
    total = int(session["total_chunks"])
    missing = [n for n in range(1, total + 1) if not (CHUNK_DIR / upload_id / str(n)).exists()]
    if missing:
        raise ValueError(f"分块不完整，缺少 {missing}（或该上传已经完成过）")

    digest = hashlib.sha256()
    merged = CHUNK_DIR / f"{upload_id}.merged"
    with merged.open("wb") as out:
        for n in range(1, total + 1):
            data = (CHUNK_DIR / upload_id / str(n)).read_bytes()
            digest.update(data)
            out.write(data)

    content_hash = digest.hexdigest()
    if content_hash != expected_hash:
        # 校验不过就把合并结果扔掉，绝不能放进 blobs/ ——
        # 一个 blob 可能被很多条元数据共用，污染了影响面很大
        merged.unlink()
        raise ValueError("合并后的内容与 initiate 时声明的 content_hash 不符")

    merged.replace(BLOB_DIR / content_hash)   # 校验通过才入库
    return content_hash


def upload_cleanup(upload_id: str) -> None:
    """删掉分块暂存目录（S3 合并完也会丢弃 parts）"""
    shutil.rmtree(CHUNK_DIR / upload_id, ignore_errors=True)
