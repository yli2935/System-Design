"""
辅助函数 —— 短网址服务的基础能力
=================================
main.py 只管路由，「怎么编码、怎么存、怎么缓存」都在这里：

    1. Base62 编码    to_base62()                          <- 文档 6.1 方案二
    2. txt 模拟数据库  load_db() / db_get() / db_insert()   <- 文档第七章
    3. 发号器          generate_code()                     <- 文档 6.1 方案二
    4. 内存模拟缓存    cache_get() / cache_set()            <- 文档 6.2 方案二
"""
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"
DB_FILE = Path(__file__).parent / "data" / "urls.txt"   # 用 txt 模拟数据库


def short_url(code: str) -> str:
    """拼出完整短链接"""
    return f"{BASE_URL}/{code}"


# ===========================================================================
# 1. Base62 编码（文档 6.1 方案二）
# ===========================================================================
# 字符集 62 个：0-9 + a-z + A-Z
ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

# 发号起点定在 62^6，保证 Base62 编码结果恒为 7 位，不会随 ID 增长变长。
# 7 位的容量 62^7 ≈ 3.5 万亿，足够文档估算的 3650 亿条记录。
ID_START = 62 ** 6


def to_base62(num: int) -> str:
    """十进制 ID -> Base62 短码。例：11157 -> '2TX'

        11157 // 62 = 179 余 59 -> 'X'
          179 // 62 = 2   余 55 -> 'T'
            2 // 62 = 0   余 2  -> '2'
    """
    code = ""
    while num > 0:
        num, remainder = divmod(num, 62)
        code = ALPHABET[remainder] + code   # 余数从低位产生，所以往前拼
    return code


# ===========================================================================
# 2. “数据库”：本地 txt 文件（文档第七章）
# ===========================================================================
# 内存索引：short_code -> long_url
# 模拟数据库在 short_url 列上的索引，让查询是 O(1) 而不是全表扫描。
_db: dict[str, str] = {}


def load_db() -> int:
    """启动时把 txt 全量读进内存索引，返回载入条数"""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DB_FILE.exists():
        DB_FILE.write_text("# short_code\tlong_url\n", encoding="utf-8")
        return 0

    for line in DB_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        code, long_url = line.split("\t", 1)
        _db[code] = long_url
    return len(_db)


def db_insert(code: str, long_url: str) -> None:
    """写入一条映射：txt 末尾追加一行（不重写整个文件），再更新内存索引"""
    with DB_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{code}\t{long_url}\n")
    _db[code] = long_url


def db_get(code: str) -> str | None:
    """按短码查长链接（走内存索引，O(1)）"""
    return _db.get(code)


def db_find_code(long_url: str) -> str | None:
    """按长链接反查短码，用于「同一长链接复用同一短码」"""
    for code, url in _db.items():
        if url == long_url:
            return code
    return None


def db_all() -> list[dict]:
    """列出全部映射"""
    return [{"short_code": code, "long_url": url} for code, url in _db.items()]


def db_count() -> int:
    return len(_db)


# ===========================================================================
# 3. 发号器（文档 6.1 方案二）
# ===========================================================================
def generate_code() -> str:
    """取一个唯一 ID，编码成短码。

    ID = 起点 + 已有记录数；真实系统里这一步是 Redis 的 `INCR url_counter`
    （多个写服务实例共享一个计数器，才能保证全局唯一）。
    ID 唯一 => 短码天然不冲突，不需要查库判重。
    """
    return to_base62(ID_START + db_count())


# ===========================================================================
# 4. 缓存（文档 6.2 方案二，这里用 dict 模拟 Redis）
# ===========================================================================
# 短网址是典型的读多写少（读:写 ≈ 10:1 甚至更高），所以读路径必须先走缓存。
_cache: dict[str, str] = {}
_stats = {"hit": 0, "miss": 0}


def cache_get(code: str) -> str | None:
    """查缓存，顺手记一次命中/未命中"""
    long_url = _cache.get(code)
    _stats["hit" if long_url else "miss"] += 1
    return long_url


def cache_set(code: str, long_url: str) -> None:
    _cache[code] = long_url


def cache_stats() -> dict:
    """命中率，给 /urls 展示用"""
    total = _stats["hit"] + _stats["miss"]
    return {**_stats, "hit_rate": round(_stats["hit"] / total, 2) if total else 0}
