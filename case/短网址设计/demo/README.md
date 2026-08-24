# 短网址服务 Demo (FastAPI)

`../短网址设计.md` 的教学版实现。**不用真实数据库、不用 Redis** —— 存储用本地 txt 文件，
缓存用 Python dict。只有 3 个 API，代码分两层：

```
demo/
├── main.py        # 路由层：3 个 API，只描述请求怎么流转
├── helper.py      # 基础能力：Base62 编码 / txt 存储 / 发号器 / 缓存
├── requirements.txt
└── data/urls.txt  # “数据库”
```

## 运行

```bash
cd case/短网址设计/demo
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

uvicorn main:app --reload
```

打开 http://127.0.0.1:8000/docs 就能直接点着试。

## 三个 API

| 方法 | 路径 | 作用 |
| ---- | ---- | ---- |
| POST | `/shorten` | 长链接 -> 短链接（写路径） |
| GET | `/{short_code}` | 302 跳转到长链接（读路径） |
| GET | `/urls` | 看 txt 里存了什么 + 缓存命中率 |

```bash
# 1. 缩短
curl -X POST http://127.0.0.1:8000/shorten \
  -H 'Content-Type: application/json' \
  -d '{"long_url": "https://en.wikipedia.org/wiki/Systems_design"}'
# {"short_url":"http://127.0.0.1:8000/1000000","short_code":"1000000","created":true}

# 2. 跳转
curl -i http://127.0.0.1:8000/1000000
# HTTP/1.1 302 Found
# location: https://en.wikipedia.org/wiki/Systems_design

# 3. 查看
curl http://127.0.0.1:8000/urls
```

## 核心三件事

### 1. 短码怎么生成 —— 发号器 + Base62（文档 6.1 方案二）

```
长链接 -> 发号器给一个唯一 ID -> Base62 编码 -> 短码
```

**为什么不用哈希（MD5 取前 7 位）？** 会冲突，每次都得查库判重。
而 ID 唯一 ⇒ 短码天然唯一，**完全不用判重**。

字符集 62 个（`0-9a-zA-Z`），发号起点定在 `62^6`，所以短码**恒为 7 位**，
不会随 ID 增长变长。7 位容量 `62^7 ≈ 3.5 万亿`，覆盖文档估算的 3650 亿条记录。

```python
helper.to_base62(11157)   # -> '2TX'
```

demo 里 `helper.generate_code()` 就是 `ID = 起点 + 已有记录数`；真实系统里这一步是
Redis 的 `INCR url_counter`（多个写服务实例共享一个计数器才能保证全局唯一）。

### 2. 数据存在哪 —— txt + 内存索引（文档第七章）

`data/urls.txt` 一行一条映射，制表符分隔：

```
# short_code	long_url
1000000	https://en.wikipedia.org/wiki/Systems_design
1000001	https://fastapi.tiangolo.com/
```

两个关键点，对应真实数据库的两件事：

- **写入只在文件末尾追加一行**（`helper.db_insert()`），不重写整个文件 —— 对应数据库的顺序写
- **启动时全量读进内存 `dict`**（`helper.load_db()`）—— 对应 `short_url` 列上的索引，
  让查询是 O(1) 而不是全表扫描（文档 6.2 方案一）

### 3. 重定向怎么变快 —— 缓存（文档 6.2 方案二）

短网址是典型的**读多写少**（读:写 ≈ 10:1 甚至更高），所以读路径必须先走缓存：

```
GET /{code} -> 查缓存 -> 命中：直接跳（内存访问 <1ms）
                     -> 未命中：查 txt -> 回填缓存 -> 跳
```

`helper.cache_get()` 顺手记命中/未命中，`/urls` 里能看到命中率：第一次访问 miss，之后全是 hit。
写入时也顺手回填缓存，所以新链接第一次点击就能命中。

**为什么用 302 不用 301？**（文档 5.3）301 会被浏览器缓存，后续点击直接去长链接、
不再回源，服务器压力小但**统计不到点击量**。想统计就用 302。

## demo 简化掉的部分

这些是文档里讨论过、但教学 demo 里不体现的：

- 自定义别名、过期时间、点击统计、删除
- Redis 计数器批量分配（一次申请 1000 个 ID，减少中心节点访问）
- 读写服务分离部署、水平扩展、数据库主从复制与分片
- LRU 淘汰与 TTL（这里的 dict 缓存不淘汰）、CDN 边缘缓存
