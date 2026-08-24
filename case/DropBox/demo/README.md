# Dropbox Demo (FastAPI)

`../DropBox.md` 的教学版实现。**不用真实数据库、不用 S3** —— 每张数据库表是一个 txt 文件，
文件内容放本地目录当 Blob Storage。代码分两层：

```
demo/
├── main.py          # 路由层：9 个 API，只描述请求怎么流转
├── helper.py        # 基础能力：txt 表读写 / Blob 存储 / 预签名 URL / 分块上传
├── requirements.txt
└── data/
    ├── files.txt    # 文件元数据表   （真实环境：DynamoDB）
    ├── shares.txt   # 共享关系表
    ├── changes.txt  # 变更日志表     （真实环境：Kafka）
    ├── users.txt    # 用户表
    ├── uploads.txt  # 上传会话表     （分块上传用）
    ├── chunks.txt   # 已到达的分块   （真实环境：S3 ListParts）
    ├── blobs/       # 完整文件内容   （真实环境：S3 + CloudFront）
    └── chunks/      # 分块暂存区，合并后删掉（真实环境：S3 multipart parts）
```

## 运行

```bash
cd case/DropBox/demo
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

uvicorn main:app --reload
```

打开 http://127.0.0.1:8000/docs。用户身份走 `X-User-Id` header（文档 2.2），
换个值就等于换个用户登录，方便演示共享。

## 九个 API

上传是**分块 + 预签名**（文档 3.1 方案三 + 4.1），文件字节一次都不经过应用服务器：

| 方法 | 路径 | 作用 | 文档 |
| ---- | ---- | ---- | ---- |
| POST | `/files/initiate` | 开上传会话，拿到每一块的通行证 | 3.1 / 4.1 |
| PUT | `/chunk/{upload_id}/{chunk_no}` | **模拟 S3**，一块一块直传 | 4.1 |
| GET | `/uploads/{upload_id}` | 还缺哪些块（断点续传 / 进度条） | 4.1 |
| POST | `/files/complete` | 合并分块 + 校验 + 写元数据 | 4.1 |
| GET | `/files` | 我的文件 + 共享给我的 | 3.3 |
| GET | `/files/{file_id}` | 元数据 + 预签名下载 URL | 3.2 |
| GET | `/download/{file_id}` | **模拟 S3/CDN**，下载通行证的落点 | 3.2 / 4.3.4 |
| POST | `/files/{file_id}/share` | 共享给其他用户 | 3.3 |
| GET | `/changes?since=N` | 拉取变更，用于同步 | 3.4 |

```bash
B=http://127.0.0.1:8000
HASH=$(shasum -a 256 big.bin | cut -d' ' -f1)    # 客户端本地算整文件 sha256
split -b 102400 big.bin part.                    # 切块（这里 100KB 一块，方便演示）

# ① 开会话，拿到每块的通行证
curl -X POST $B/files/initiate -H 'X-User-Id: alice' -H 'Content-Type: application/json' \
  -d "{\"name\":\"big.bin\",\"content_hash\":\"$HASH\",\"total_chunks\":3,\"chunk_size\":102400}"
# {"blob_exists":false,"upload_id":"7523c247e813",
#  "chunks":[{"chunk_no":1,"upload_url":".../chunk/7523c247e813/1?expires=...&sig=..."}, ...]}

# ② 各块直传「S3」（可以并行；失败只重传这一块）
curl -X PUT --data-binary @part.aa "<chunk_1_url>"
# {"chunk_no":1,"size":102400}

# ③ 断线重连后问还缺哪些块
curl $B/uploads/7523c247e813 -H 'X-User-Id: alice'
# {"total_chunks":3,"uploaded":[1,3],"missing":[2],"progress":"2/3"}

# ④ 全到了就合并 + 写元数据
curl -X POST $B/files/complete -H 'X-User-Id: alice' -H 'Content-Type: application/json' \
  -d '{"upload_id":"7523c247e813"}'
# {"file_id":"9be8fc695728","version":1,"size":256000,"content_hash":"b57b64b1..."}

# ⑤ alice 共享给 bob
curl -X POST $B/files/9be8fc695728/share \
  -H 'X-User-Id: alice' -H 'Content-Type: application/json' -d '{"users":["bob"]}'

# ⑥ bob 拿下载通行证，然后直接下载（不带 X-User-Id 也能下，签名就是凭证）
curl $B/files/9be8fc695728 -H 'X-User-Id: bob'
curl "<download_url>" -o got.bin

# ⑦ bob 的设备同步
curl "$B/changes?since=0" -H 'X-User-Id: bob'
```

## 核心五件事

### 1. 元数据和文件内容分开存（文档 3.1）

这是这道题最核心的一点：

```
文件内容 -> data/blobs/{sha256}   非结构化、体积大、只按 key 取   -> S3
元数据   -> data/files.txt        结构化、要查询、体积小          -> DynamoDB
```

元数据里只留一个 `content_hash` 指向内容，所以元数据表永远很小、查询很快，
50GB 的大文件不会跟着元数据一起被查出来。

`data/files.txt` 长这样：

```
# file_id	name	size	mime_type	content_hash	owner	version	updated_at
6730031d1766	a.txt	14	text/plain	ee48cd56...	alice	1	2026-08-21T20:51:30
69f056712c22	copy.txt	14	text/plain	ee48cd56...	alice	1	2026-08-21T20:51:30
6730031d1766	a.txt	11	text/plain	b8bfaed9...	alice	2	2026-08-21T20:51:30
```

三行元数据，但 `blobs/` 里**只有两个文件** —— 见下面的去重和版本号。

### 2. 预签名 URL：文件字节不经过应用服务器（文档 3.1 方案三、4.3.4）

这是这道题的招牌考点。**上传和下载都不让文件穿过应用服务器**，服务器只负责发通行证。

```
上传（四步）                                   下载（两步）
①  POST /files/initiate  -> 每块一张通行证     ①  GET /files/{id} -> download_url
②  PUT  chunk_url × N  (字节直接进 S3)         ②  GET download_url (字节直接从 S3 出来)
③  GET  /uploads/{id}    (还缺哪些块)
④  POST /files/complete  (合并 + 写元数据)
```

通行证就是一个带签名和过期时间的 URL：

```
/download/6730031d1766?expires=1787360206&sig=7f7e8f15ad2c4011
                       └─ 过期时间戳 ─┘   └─ HMAC 签名 ─┘
```

签名是 `HMAC-SHA256(密钥, "方法:资源key:过期时间")`。没有密钥就伪造不出来，改一个字符就验不过。
**签名里带上 HTTP 方法**，所以一张下载通行证不能拿去上传 —— 真实的 S3 预签名也是这么做的。

下载的权限在上一步 `GET /files/{file_id}` 就检查过了（不是 owner 也没被共享 → 403）；
上传的元数据要等最后 `complete` 那步才写。

`PUT /chunk/{id}/{no}` 和 `GET /download/{file_id}` 这两个接口扮演的是
**S3/CDN，不是应用服务器**，所以它们只验签名，完全不看 `X-User-Id` ——
S3 不认识你的用户系统，签名就是凭证。

**为什么不能图省事直接 `POST /files` 把文件传给应用服务器？**（文档 3.1 方案二）
因为 50GB 的文件要穿过应用服务器两次（客户端 → 服务器 → S3），
服务器带宽、内存和延迟全部白白翻倍，而应用服务器是最难扩容的一环。

### 3. 共享关系单独一张表（文档 3.3 方案二）

`data/shares.txt`：

```
# file_id	shared_with	shared_by	shared_at
6730031d1766	bob	alice	2026-08-21T20:51:46
```

**为什么不在元数据里塞一个 `sharedWith: ["bob"]` 数组？**（方案一）
因为查询是双向的：

- 「这个文件共享给了谁」—— 数组能查
- 「哪些文件共享给了我」—— 数组就得扫全表，逐行比对数组内容

单独一张表，`(file_id, shared_with)` 两个方向都能建索引，两种查询都快。

### 4. 同步靠变更日志 + 版本号（文档 3.4）

`data/changes.txt` 是一个自增游标的事件流：

```
# seq	file_id	owner	version	event	updated_at
1	6730031d1766	alice	1	created	...
2	69f056712c22	alice	1	created	...
3	6730031d1766	alice	2	updated	...
```

客户端记住自己的 `cursor`，下次带 `?since=3` 只拉新变更，比对 `version` 决定要不要重新下载
（文档 3.4 元数据更新追踪）。返回时会按可见性过滤 —— bob 拉到的只有共享给他的那个文件，
上面例子里 `seq=2` 就被过滤掉了。

demo 用的是**客户端带游标轮询**（方案一）；真实系统是变更事件进 Kafka →
同步服务订阅 → WebSocket 主动推给用户的其他设备（方案三）。

### 5. 分块上传：大文件的关键（文档 4.1）

50GB 单次 PUT 有四个麻烦：传一个多小时、中途断线要从 0 重来、没法显示进度、单连接吃不满带宽。
切成小块分别传，四个问题一起解决：

| 好处 | demo 里怎么体现 |
| ---- | -------------- |
| **断点续传** | `GET /uploads/{id}` 返回 `missing:[2]`，只补传第 2 块 |
| **并行上传** | initiate 一次返回所有块的通行证，客户端可以同时传 |
| **进度显示** | `progress: "2/3"` |
| **失败隔离** | 一块失败只重传那一块 |

块大小默认 5MB —— 这是 S3 Multipart Upload 规定的最小值（最后一块除外），
最多 10,000 块，所以单文件上限约 5TB。

两张新表支撑这件事：

```
# data/uploads.txt —— 上传会话（谁在传什么，应用服务器的账本）
# upload_id	name	mime_type	content_hash	chunk_size	total_chunks	owner	created_at
7523c247e813	big.bin	application/octet-stream	b57b64b1...	102400	3	alice	2026-08-21T21:46:36

# data/chunks.txt —— 哪些块已经到了（断点续传靠它）
# upload_id	chunk_no	size	uploaded_at
7523c247e813	1	102400	...
7523c247e813	3	51200	...
7523c247e813	2	102400	...      <- 断线后补传的那一块，乱序到达没关系
```

严格说 `chunks.txt` 属于「S3」那边的账本（真实环境是 S3 的 `ListParts` 接口），
demo 里为了简单跟应用服务器的表放在一起了。

**三个容易漏掉的实现细节**，demo 里都做了：

1. **流式写盘**。`PUT /chunk/...` 用的是 `async for part in request.stream()` 边收边写。
   如果写成 `await request.body()`，整块都会先进内存 —— 5MB 的块还行，
   但这个错误一旦发生在不分块的接口上，传 50GB 就直接 OOM

2. **合并时也一块一块读**。`helper.upload_finalize()` 顺序读每块、边算 sha256 边写出，
   内存里同时只有一块（5MB），所以 50GB 的文件也合得动

3. **校验通过才入库**。合并结果先写成临时文件，算出的 sha256 跟 initiate 时声明的一致
   才移进 `blobs/`；对不上就删掉临时文件报 400。
   为什么不能先放进去再说？因为去重意味着**一个 blob 可能被很多条元数据共用**，
   污染一个 blob 的影响面很大

### 附：内容去重 = 秒传（文档 4.2.4）

blob 的文件名就是内容的 sha256，所以相同内容天然只存一份。而因为**客户端是先在本地
算好 hash 才来要通行证的**，服务器可以在第 1 步就回答「这份内容我已经有了」：

```bash
curl -X POST $B/files/initiate -d "{\"name\":\"big.bin\",\"content_hash\":\"$HASH\", ...}"
# {"blob_exists":true,"file_id":"c7fa6ced5960","version":1}   <- 秒传：一个字节都不用传
```

服务器发现内容已经有了，当场把元数据写好、返回 `file_id`，**分块上传那三步全部跳过**。
真实的 Dropbox 上传常见文件那么快，就是这个原因。两条元数据共用一个 blob，
`blobs/` 里不会多出文件 —— 一百个人上传同一个安装包，也只占一份空间。

### 附：同名再上传 = 新版本

`file_id = md5(owner + name)`，所以同一个用户上传同名文件拿到的是同一个 `file_id`，
`files.txt` 里追加一条 `version + 1` 的新记录。读的时候按 `file_id` 取 version 最大的那条
（`helper.files_latest()`）—— 表本身是追加写的版本日志，旧版本都还留着。

## demo 简化掉的部分

文档里讨论过、但这里没做的：

- **WebSocket + Kafka 实时推送**（文档 3.4 方案三），demo 用轮询代替
- **块级去重 / 增量同步**（文档 4.2.3）—— demo 只对整个文件去重。真实 Dropbox 按块去重，
  改一个 50GB 文件的开头 5MB，只需要重传那一块
- **废弃上传的清理** —— 传了一半就放弃的上传会在 `data/chunks/` 里留下分块。
  这跟真实 S3 的行为一致（parts 会一直留着），真实环境靠 S3 lifecycle 规则
  （`AbortIncompleteMultipartUpload`）定期清理
- **块大小的强制校验** —— S3 会拒绝小于 5MB 的非末块，demo 只记录 `chunk_size` 不做校验
- CDN 边缘缓存、增量同步（rsync 差异算法）、压缩
- 删除文件、回收站、引用计数（同一个 blob 被多条元数据引用时不能直接删）
- 真实的认证授权（JWT / RBAC），demo 直接信任 `X-User-Id` header
