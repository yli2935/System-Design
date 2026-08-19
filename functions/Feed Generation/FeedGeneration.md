# Feed 生成（Feed Generation）—— Push / Pull / Hybrid 三种策略

> 社交产品 Home Timeline 的生成与分发。本文按 **HelloInterview 交付模式**（Requirements → Core Entities → API → High-Level Design → Deep Dives）组织，
> 核心是回答一个问题：**一条推文如何抵达它的所有粉丝？**

## 目录

- **一、问题理解与场景定义** —— Home Timeline vs User Timeline，N 路归并的本质
- **二、需求分析** —— 功能/非功能需求、容量估算（⭐ 115 万扇出写 QPS）
- **三、核心实体** —— User / Tweet / Follow / Feed
- **四、API 设计** —— 发帖、拉 Feed（游标分页）、关注/取关
- **五、策略一：Push Model（Fan-out on Write）** —— 写时扇出，读极快，死于 Celebrity Problem
- **六、策略二：Pull Model（Fan-out on Read）** —— 读时归并，写极快，死于读放大
- **七、策略三：Hybrid Model（混合方案）** ⭐ —— 生产环境的标准答案
- **八、深入探讨（Deep Dives）** —— 存储选型、Backfill、删帖同步、尾延迟、一致性、分片、容灾、监控
- **九、面试实战指南** —— 答题路径、级别期望、12 条高频追问、扣分项、Cheat Sheet
- **十、参考资料**

---

## 📌 全文统一场景设定

| 参数 | 数值 |
| --- | --- |
| 产品形态 | 类 Twitter/X 的单向关注社交 Feed |
| 日活用户 DAU | **2 亿** |
| 平均关注数 / 平均粉丝数 | 200 / 200（长尾分布） |
| 大 V（粉丝 > 100 万） | ~1 万个账号，头部 **1 亿+** 粉丝 |
| 发帖量 | 5 亿条/天 → 平均写 **6,000 QPS**，峰值 **5 万 QPS** |
| 读 Feed | 300 亿次/天 → 平均读 **35 万 QPS**，峰值 **100 万 QPS** |
| 读写比 | **60 : 1**（严重读多写少） |
| Feed 首屏 | 20 条，支持下拉分页 |
| 延迟目标 | Home Timeline **P99 < 200ms** |

> ⭐ **贯穿全文的核心矛盾**：5 亿推文/天 × 200 粉丝 = **1000 亿次扇出写入/天 ≈ 115 万写 QPS**。
> 后面所有的设计，都是在跟这个数字搏斗。

---

## 一、问题理解与场景定义

### 1.1 什么是 Feed / Timeline？

Feed（信息流）是社交产品的核心界面：用户打开 App 看到的那条**按时间倒序排列的、由多个内容源聚合而成的列表**。

在类 Twitter/X 的产品里，「Timeline」这个词有两种完全不同的含义，**混淆这两者是系统设计面试中最常见的失分点**。面试开场第一句就应该主动澄清：

**Home Timeline（主页时间线 / 关注流）**

- 用户 A 打开 App 看到的首页
- 内容 = A 关注的 200 个人**各自发的推文**，按时间倒序**归并**成一条流
- 这是一个**聚合（aggregation）**结果，数据不属于 A，A 只是消费者

**User Timeline（个人主页时间线 / 作者流）**

- 点进用户 B 的头像，看到的 B 的主页
- 内容 = B **自己发的**推文，按时间倒序
- 这是一个**单源查询**，`SELECT * FROM tweets WHERE author_id = B ORDER BY created_at DESC LIMIT 20`

> 💡 **核心思想**：User Timeline 是一个**单表点查**问题，几乎不需要设计；Home Timeline 是一个**分布式 N 路归并**问题，整个 Feed 系统 95% 的复杂度都在这里。面试中说清这一点，等于告诉面试官「我知道真正的难点在哪」。

### 1.2 Home Timeline vs User Timeline 对比

| 维度 | Home Timeline（关注流） | User Timeline（个人主页） |
|------|------------------------|--------------------------|
| **数据来源** | N 个关注对象的推文聚合（平均 N=200） | 单一作者自己的推文（N=1） |
| **查询形态** | N 路归并排序（分布式 scatter-gather） | 单分区范围扫描（`WHERE author_id=X`） |
| **读取频率** | 极高，每天 300 亿次，占总读量 ~90% | 低，每天约 30 亿次，占 ~10% |
| **生成难度** | ⭐⭐⭐⭐⭐ 核心难题 | ⭐ 几乎无难度 |
| **是否可缓存** | ✅ 必须缓存/预计算，但**每个用户一份**，2 亿份 | ✅ 天然可缓存，**全局共享一份**，命中率极高 |
| **缓存失效** | ⚠️ 关注对象每发一条推就要更新 → 写放大 | 作者自己发推才变 → 写放大为 0 |
| **一致性要求** | 弱，晚几秒看到无所谓 | 稍强，作者发完自己刷新应该立刻看到（read-your-writes） |
| **数据可分片键** | 按 `user_id`（消费者）分片 | 按 `author_id`（生产者）分片 |
| **典型 P99 目标** | < 200ms | < 100ms |

⚠️ 注意最后两行：**两者的天然分片键是相反的**。推文按 `author_id` 存储最自然（作者主页快），但 Home Timeline 需要按 `user_id`（读者）聚合。这个「生产者分片 vs 消费者分片」的错位，正是 Fan-out 策略之争的物理根源。

### 1.3 Feed 生成的本质：一个 N 路归并（N-way Merge）问题

抽象掉所有业务，Home Timeline 就是：

```
给定 N 个已按时间倒序排好的有序列表（每个关注对象的 User Timeline），
求这 N 个列表归并后的 Top-K（K = 20，首屏 20 条）。
```

这是经典的 **N-way merge / Top-K merge** 问题。这是经典的 **N-way merge / Top-K merge** 问题。用最大堆（max-heap；Python 的 `heapq` 是最小堆，对 tweet_id 取负号模拟）做归并，时间复杂度 `O(N + K log N)`（heapify 建堆 O(N)，再弹出 K 次、每次 O(log N)）：，时间复杂度 `O(N log N + K log N)`：

- N = 200（平均关注数）
- 每个列表要从数据库拉取至少 K=20 条候选（因为极端情况下 20 条全来自同一个人）
- 单次读 Feed = **200 次数据库查询 + 4000 条候选记录的堆归并**

如果这 200 个作者散落在 100 个数据库分片上，P99 延迟 = **最慢那个分片的延迟**（尾延迟放大，tail latency amplification）。假设单分片 P99 = 20ms，200 次并发查询取最大值，实际 P99 会退化到 100ms+，加上归并、水合（hydration，把 tweet_id 换成正文）、网络，**总延迟轻松突破 500ms**，直接击穿 200ms 的 SLA。

**根本矛盾：写放大 vs 读放大**

这个问题只有两条路，且两条路各自都会爆炸：

| 策略 | 何时做归并 | 代价形态 | 量级（本场景） |
|------|-----------|---------|---------------|
| **Fan-out on Read**（拉模型 / Pull） | 用户读 Feed 时实时归并 | **读放大**：1 次读 → 200 次查询 | 35 万读 QPS × 200 = **7000 万次查询/秒** ❌ |
| **Fan-out on Write**（推模型 / Push） | 发推时就推给所有粉丝 | **写放大**：1 次写 → 200 次写入 | 6000 写 QPS × 200 = **115 万次写入/秒** ⚠️ |

> 💡 **核心思想**：Feed 生成不存在「没有放大」的方案。你只能选择**把成本付在写路径还是读路径**。因为读写比是 60:1，把成本前置到写路径（Fan-out on Write）在总量上更划算 —— 115 万 < 7000 万，差 60 倍。**这就是绝大多数 Feed 系统默认选择推模型的原因，也是「读多写少就把计算搬到写路径」这一通用原则的最佳案例。**

但推模型有它的死穴：**Celebrity Problem**（大 V 问题）。一个 1 亿粉丝的账号发一条推，需要 1 亿次写入 —— 这是后续章节的主战场。

### 1.4 ASCII 图：500 个关注对象如何归并成一条时间线

以一个重度用户 Alice（关注了 500 人，高于 200 的平均值）为例：

```
                          【N 路归并 (N = 500)】

  Alice 关注的 500 个作者，每人一条按时间倒序的 User Timeline
  ┌──────────────────────────────────────────────────────────────────┐
  │                                                                  │
  │  作者 U1   ▸ [t=10:32 "A1"] [t=09:14 "A2"] [t=08:01 "A3"] ...    │  DB Shard 3
  │  作者 U2   ▸ [t=10:35 "B1"] [t=07:22 "B2"] ...                   │  DB Shard 17
  │  作者 U3   ▸ [t=10:31 "C1"] [t=10:28 "C2"] [t=06:55 "C3"] ...    │  DB Shard 3
  │  作者 U4   ▸ [t=09:58 "D1"] ...                                  │  DB Shard 41
  │     ⋮                                                            │      ⋮
  │  作者 U500 ▸ [t=10:34 "Z1"] [t=02:11 "Z2"] ...                   │  DB Shard 88
  │                                                                  │
  └──────────────────────────────────────────────────────────────────┘
        │        │        │        │                    │
        │  每路各取 Top-20 候选（保证正确性的最小取数）    │
        ▼        ▼        ▼        ▼                    ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │              最小堆归并 Min-Heap (size = 500)                     │
  │        │              最大堆归并 Max-Heap (size = 500)                     │
  │      每次弹出 timestamp 最大的一条，压入该路的下一条              │              │
  │      复杂度 O(K · log N) = 20 × log2(500) ≈ 20 × 9 = 180 次比较   │
  └──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                【Alice 的 Home Timeline · 首屏 20 条】
        ┌────────────────────────────────────────────────┐
        │  1. t=10:35  U2   "B1"                         │
        │  2. t=10:34  U500 "Z1"                         │
        │  3. t=10:32  U1   "A1"                         │
        │  4. t=10:31  U3   "C1"                         │
        │  5. t=10:28  U3   "C2"    ← 同一作者可连续出现  │
        │     ⋮                                          │
        │ 20. t=09:14  U1   "A2"                         │
        └────────────────────────────────────────────────┘
                                │
                                ▼  cursor = (09:14, tweet_id) 用于下一页
```

**读放大的可视化**：

```
  Alice 一次下拉刷新（1 个 HTTP 请求）
        │
        ├─────────► 500 次并发 DB 查询（散落在 ~100 个分片）
        │           拉回 500 × 20 = 10,000 条候选记录
        │
        ├─────────► 堆归并丢弃 9,980 条，只保留 20 条  ← 99.8% 的工作白做了
        │
        └─────────► 20 次 hydration（tweet_id → 正文/作者/计数）

  ❌ 有效产出率 = 20 / 10,000 = 0.2%
```

> ⚠️ **面试中怎么说**：「这里最刺眼的数字是 **0.2% 的有效产出率** —— 我们拉了一万条数据只为了返回二十条。这说明实时归并的边际成本极高，而且它是**每次读都要重复付**的。既然一个用户的 Home Timeline 在两次发推之间是不变的，那这份归并结果就应该被**物化（materialize）并复用** —— 这就自然引出 Fan-out on Write 的预计算方案。」

---

## 二、需求分析（Requirements）

### 2.1 功能需求（Functional Requirements）

**核心 3 条（面试中必须限定在这个范围内，30-45 分钟只够讲透这三条）：**

1. ✅ **发帖（Post a Tweet）**：用户可以发布一条不超过 280 字符的推文，发布后应「立即」出现在自己的 User Timeline 中（read-your-writes），并在秒级内出现在粉丝的 Home Timeline 中。
2. ✅ **看 Feed（View Home Timeline）**：用户可以拉取自己的 Home Timeline，首屏 20 条，按时间倒序，支持下拉加载更多（无限滚动）。
3. ✅ **关注 / 取关（Follow / Unfollow）**：用户可以关注或取消关注另一个用户，关注关系变更后新的 Feed 应反映这一变化。

**明确划出 Out of Scope（❌ 不做的）：**

| 不做的功能 | 为什么在面试中主动排除 |
|-----------|---------------------|
| ❌ **推荐算法 / 排序（Ranking）** | ML 排序（EdgeRank、双塔召回）是另一个 45 分钟的题目。**先做纯时序（chronological）Feed**，把 Fan-out 讲透，最后作为 extension 提一句「排序层可以插在 hydration 之后」 |
| ❌ **广告插入（Ad Injection）** | 属于变现系统，与 Feed 生成正交，通常在渲染层做位置注入 |
| ❌ **私信 DM** | 是 point-to-point 消息系统，模型完全不同（有已读回执、端到端加密），不属于 Feed |
| ❌ **通知系统（Notifications）** | 虽然也有 fan-out，但量级和一致性要求不同 |
| ❌ **搜索 / 话题标签（Search / Hashtag）** | 需要倒排索引 + Elasticsearch，独立子系统 |
| ❌ **媒体上传与转码** | 图片/视频走 CDN + 对象存储，推文里只存 `media_url` |
| ❌ **点赞/转发计数的强一致** | 计数用最终一致的 counter service，允许短暂不准 |

> 💡 **面试中怎么说**：「我想把范围收敛到时序 Feed 的生成与分发。推荐排序当然是真实产品的关键，但它的瓶颈在特征和模型服务，而这道题真正的分布式系统难点在 **fan-out 的写放大**和 **Celebrity Problem**。如果时间允许，最后我可以补充排序层怎么挂上去。」——**主动收窄范围是 Senior/Staff 的信号，不是回避。**

### 2.2 非功能需求（Non-Functional Requirements）

#### 2.2.1 低延迟（Low Latency）

- 🎯 **Home Timeline 首屏 P99 < 200ms**（服务端耗时，不含客户端网络）
- 这个数字直接杀死了「实时 N 路归并」方案：200 个分片并发查询的尾延迟叠加已经吃掉预算的一半以上
- 200ms 的预算拆解（后续章节会反复用这张表）：

| 阶段 | 预算 | 说明 |
|------|------|------|
| 网关 + 鉴权 | 10ms | JWT 校验，本地缓存公钥 |
| 读预计算 Feed（Redis `ZREVRANGE`） | 5ms | 单次 Redis 调用，同机房 |
| 合并大 V 推文（Pull 部分） | 30ms | 拉 ~50 个大 V 的最新推文并归并 |
| Hydration（批量取 20 条正文 + 作者信息） | 40ms | `MGET` 批量，避免 N+1 |
| 过滤（拉黑、删除、隐私） | 15ms | Bloom filter 前置 |
| 序列化 + 网络 | 20ms | JSON/Protobuf |
| **缓冲余量** | 80ms | 应对 GC、抖动、重试 |

#### 2.2.2 高可用性（High Availability）—— 选 AP 而非 CP

**结论：Feed 系统是典型的 AP 系统，可用性 99.99%（年停机 < 53 分钟）。**

为什么 Feed 场景可以接受最终一致性、几秒延迟不致命？

1. **用户没有「正确 Feed」的参照物**
   - 用户根本不知道「此刻本应该有 37 条新推文」。如果他只看到 35 条，感知为零。
   - 对比转账：少了 100 块钱用户立刻能发现 → 那才需要 CP。
2. **信息本身就是无界流，延迟被自然吸收**
   - Feed 是无限滚动的流，「晚 3 秒出现」和「刷新时才出现」在用户体验上无法区分。
   - 而且发推 → 粉丝真正打开 App 之间，天然有分钟级的时间差。
3. **不可用的代价远大于不一致的代价**
   - Feed 报错 = 白屏 = 用户流失，这是产品级事故。
   - Feed 少 2 条 = 用户下拉一次就补上了，零成本。
4. **写路径本来就是异步的**
   - Fan-out 走消息队列（Kafka），本质上就放弃了同步一致性。强行做同步 fan-out 意味着发一条推要等 200（甚至 1 亿）次写入完成才返回 —— 发推延迟从 50ms 变成分钟级，产品直接不可用。

⚠️ **唯一的例外：read-your-writes（读己之写）**
用户自己发完推，刷新主页必须立刻看到自己的推文，否则会以为发送失败并重复发送。这一条要**单独保证**（客户端乐观插入，或服务端把自己的最新推文单独 merge 进 Feed 头部），不能靠最终一致性糊弄过去。

#### 2.2.3 可扩展性与读写比

- 📈 **可扩展性**：支持 2 亿 DAU、5 亿推文/天，且要能水平扩展到 5 倍（不需要架构重写）
- 📊 **读多写少 ≈ 60 : 1**（300 亿读 / 5 亿写）—— 这个比例是所有设计决策的锚点
- 🔥 **热点倾斜（Skew）**：粉丝数是极端长尾分布，平均 200 但头部 1 亿+，**平均值在这里是有害的**，必须按分布设计

#### 2.2.4 汇总表：需求 → 指标 → 对设计的影响

| 需求 | 具体指标 | 对设计的影响 |
|------|---------|------------|
| **低延迟** | Home Timeline P99 < 200ms<br>发推 P99 < 500ms | ❌ 排除读时实时归并<br>✅ 必须预计算 + Redis 内存存储<br>✅ 发推走「同步落库 + 异步 fan-out」 |
| **高可用** | 99.99%，AP over CP | ✅ 多副本 + 跨 AZ 部署<br>✅ Fan-out 走 Kafka 异步，允许秒级延迟<br>✅ Redis 挂了要能降级回 Pull 模式（graceful degradation） |
| **最终一致** | Fan-out 延迟 P99 < 5s | ✅ Kafka 消费者可水平扩展<br>✅ 允许乱序到达，靠 ZSET 的 score 重排 |
| **读己之写** | 自己发的推 < 100ms 可见 | ⚠️ 单独通路：写自己的 timeline 同步完成，或客户端乐观更新 |
| **读多写少 60:1** | 读 35 万 QPS / 写 6000 QPS | ✅ 把计算搬到写路径（Fan-out on Write）<br>✅ 读路径几乎纯缓存命中 |
| **写放大** | 峰值 fan-out **115 万写入/秒** | ⚠️ 这是全系统最大压力点<br>✅ 必须削峰（Kafka）+ 批量写（pipeline）+ 只写活跃用户 |
| **热点倾斜** | 1 万个大 V，头部 1 亿粉丝 | ✅ **混合模式**：普通用户 Push，大 V Pull<br>✅ 读时合并（merge-on-read） |
| **存储成本** | Feed 缓存 ~4TB，推文 55TB/年 | ✅ Feed 只存 ID 不存正文<br>✅ 只为活跃用户预计算<br>✅ 冷数据下沉对象存储 |
| **可扩展** | 支持 5 倍增长 | ✅ 无状态服务 + 一致性哈希分片<br>✅ 分片键选择要避免重分片 |

### 2.3 容量估算（Back-of-the-envelope Estimation）

> 💡 **面试中怎么说**：估算不是为了算出精确数字，而是为了**找到那个把方案逼到墙角的数字**。这道题里，那个数字是 **115 万写 QPS**。

#### 2.3.1 基础参数（全文统一，不得更改）

| 参数 | 数值 |
|------|------|
| 日活用户 DAU | 2 亿（2 × 10⁸） |
| 平均关注数 | 200 |
| 平均粉丝数 | 200（长尾分布） |
| 大 V（粉丝 > 100 万） | ~1 万个账号，头部 1 亿+ 粉丝 |
| 每天发推量 | 5 亿条（5 × 10⁸） |
| 每天读 Feed 次数 | 300 亿次（3 × 10¹⁰） |
| 一天的秒数 | 86,400 ≈ **10⁵**（估算时用 10 万秒，误差 15%，可接受） |

#### 2.3.2 写 QPS（发推）

```
平均写 QPS = 5 亿条/天 ÷ 86,400 秒
           = 5 × 10⁸ / 8.64 × 10⁴
           = 5,787
           ≈ 6,000 QPS

峰值写 QPS = 平均 × 8（社交产品晚高峰 + 热点事件突发系数）
           ≈ 50,000 QPS
```

✅ **结论：6000 写 QPS 本身毫无压力**。一个分片良好的 MySQL 集群或 Cassandra 集群轻松承接。**发推本身不是难点。**

#### 2.3.3 读 QPS（拉 Feed）

```
平均读 QPS = 300 亿次/天 ÷ 86,400 秒
           = 3 × 10¹⁰ / 8.64 × 10⁴
           = 347,222
           ≈ 350,000 QPS  (35 万)

峰值读 QPS ≈ 1,000,000 QPS  (100 万，约 3 倍)

读写比 = 3 × 10¹⁰ / 5 × 10⁸ = 60 : 1  ✅ 严重读多写少
```

**如果用 Fan-out on Read（实时归并）**：

```
后端查询 QPS = 35 万 × 200（关注数）
             = 7,000 万次查询/秒

单机 MySQL 极限约 5,000 QPS（含索引点查）
需要机器数 = 7 × 10⁷ / 5 × 10³ = 14,000 台   ❌ 完全不可行
```

#### 2.3.4 ⭐ 扇出量估算 —— 全篇的核心矛盾

**如果用 Fan-out on Write（发推时推给所有粉丝）**：

```
每天 fan-out 写入次数 = 每天推文数 × 平均粉丝数
                      = 5 亿 × 200
                      = 1,000 亿次/天
                      = 1 × 10¹¹ 次/天

平均 fan-out 写 QPS = 1 × 10¹¹ / 8.64 × 10⁴
                    = 1,157,407
                    ≈ 1,150,000 QPS  ⭐⭐⭐  (115 万)

峰值 fan-out 写 QPS ≈ 115 万 × 8 ≈ 900 万 QPS  🔥
```

> ⭐ **115 万写 QPS —— 记住这个数字，它是整篇文章的核心矛盾。**
>
> 它意味着：**一个 6000 QPS 的写入，被放大了 200 倍，变成了 115 万 QPS 的后端风暴。**
> 后面所有的设计（Kafka 削峰、Redis Pipeline 批量、只推活跃用户、大 V 走 Pull、混合模式），**全部都是在跟这个数字搏斗。**

**两条路的正面对比：**

| 方案 | 后端操作量（平均） | 单次操作成本 | 可行性 |
|------|------------------|------------|--------|
| Fan-out on Read | **7,000 万 查询/秒**（磁盘/网络随机读） | 高（~1ms，跨分片） | ❌ 需 1.4 万台 DB |
| Fan-out on Write | **115 万 写入/秒**（Redis 内存追加） | 低（~0.05ms，可 pipeline 批量） | ⚠️ 需 ~300 台 Redis，可行 |

**为什么 115 万 Redis 写是可行的？**

```
单台 Redis 单线程 ~10 万 ops/s（简单命令）
使用 Pipeline 批量（每批 100 条命令）后 ~50~80 万 ops/s

需要分片数 = 115 万 / 50 万 ≈ 3 台（理论值）
实际考虑峰值 900 万 QPS + 副本 + 内存容量 + 热点隔离
     → ~100~300 个 Redis 分片（更多是被内存容量而非 QPS 逼出来的）
```

✅ **结论：Fan-out on Write 把「1.4 万台 DB」变成了「几百台 Redis」，这就是为什么它是默认方案。**

⚠️ **但是**：上面用的是**平均粉丝数 200**。真实分布是长尾的：

```
头部大 V 发一条推：
  1 亿粉丝 × 1 条推 = 1 亿次写入
  即使用 Redis Pipeline，50 万 ops/s
  → 单条推文 fan-out 耗时 = 1 × 10⁸ / 5 × 10⁵ = 200 秒 ❌

  用户会在 3 分钟后才看到 Taylor Swift 的推文，
  而她的粉丝可能已经在别处看到了转发 —— 体验崩塌。
```

> 💡 **这就是 Celebrity Problem（大 V 问题）的量化定义**：不是「大 V 粉丝多」这句空话，而是**「单条推文的 fan-out 耗时 200 秒，超出可接受的 5 秒 SLA 40 倍」**。面试中说出这个数字，比说「大 V 会有热点问题」强十倍。

#### 2.3.5 存储估算 —— Feed 缓存（只存 ID，不存正文）

**关键设计决策：Feed 缓存里只存指针，不存内容。**

```python
# ❌ 错误做法：Feed 里存完整推文
feed_entry = {
    "tweet_id": 1234567890123456789,
    "author_id": 987654321,
    "text": "今天天气真好，出去跑了 10 公里...",   # 280 字符 UTF-8 ≈ 300+ 字节
    "author_name": "张三",
    "avatar_url": "https://cdn.../a.jpg",
    "like_count": 1523,                          # 而且这个数字随时在变！
}
# 单条 ~500 字节 → 2 亿用户 × 800 条 × 500B = 80 TB  ❌ 内存放不下
# 更致命：内容重复存储 200 万次（一条爆款推被 fan-out 到 200 万人的 feed）
# 而且 like_count 变了要更新 200 万份副本 —— 不可能

# ✅ 正确做法：只存三元组指针
feed_entry = (tweet_id, author_id, timestamp)
```

**单条 Feed Entry 大小推导：**

| 字段 | 类型 | 字节 | 说明 |
|------|------|------|------|
| `tweet_id` | int64 (Snowflake) | 8 | 全局唯一 ID，本身自带时间序 |
| `author_id` | int64 | 8 | 用于读时过滤（拉黑/取关的快速剔除） |
| `timestamp` | int64 (ms) | 8 | 排序键（其实可从 Snowflake 抽取，但显式存便于排序） |
| **合计** | | **24 字节** | ⭐ |

**每用户缓存容量：**

```
每用户缓存条数 = 800 条（约 40 页 × 20 条/页）
              → 覆盖 99% 用户的滚动深度，再往下走 DB 慢路径

每用户内存 = 800 条 × 24 字节
          = 19,200 字节
          ≈ 20 KB   ⭐
```

**全量用户（悲观估算）：**

```
总内存 = 2 亿用户 × 20 KB
       = 2 × 10⁸ × 2 × 10⁴ 字节
       = 4 × 10¹² 字节
       = 4 TB   ⭐
```

⚠️ **真实成本还要乘系数**：Redis ZSET 的 skiplist + dict 结构开销约 2~3 倍（每个 member 有指针、score 是 8 字节 double、ziplist 超阈值后退化）。**实际 ≈ 8~12 TB**，再算主从副本 ×2 → **~20 TB 内存**。按每台 128GB 内存的 Redis 实例算，需要 **~160 台**，成本非常可观（云上月成本 6 位数美元）。

**优化：只给活跃用户预计算（Active User Only Fan-out）**

社交产品的用户活跃度是典型的幂律分布：

| 用户分层 | 占比 | 人数 | 策略 |
|---------|------|------|------|
| 🔥 **高活跃**（每天登录） | 20% | 4,000 万 | ✅ 全量 Push，800 条缓存 |
| 🟡 **中活跃**（7~30 天内登录） | 30% | 6,000 万 | 🟡 Push 但只留 200 条（降级缓存） |
| ❄️ **不活跃**（> 30 天未登录） | 50% | 1 亿 | ❌ 不做 fan-out，登录时按需 Pull 重建 |

```
优化后内存 = 4,000 万 × 20 KB  +  6,000 万 × 5 KB
          = 8 × 10¹¹  +  3 × 10¹¹
          = 1.1 × 10¹² 字节
          ≈ 1.1 TB   （原始 4 TB → 降到 ~1.1 TB，降低 ~73%）

带副本和结构开销后 ≈ 5~6 TB 内存，约 50 台机器  ✅ 可接受
```

**更重要的是：写放大也同步下降**

```
只推给活跃用户后的 fan-out 量：
  1000 亿次/天 × 50%（跳过不活跃粉丝）= 500 亿次/天
  → 平均 fan-out 写 QPS 从 115 万 降到 ~58 万   ✅ 压力减半

💡 这是一石二鸟的优化：省内存的同时省了一半的写放大。
   面试中主动提出这一点，是非常强的加分项。
```

⚠️ **代价**：不活跃用户回归时首次加载 Feed 会走慢路径（实时归并 200 路），P99 可能到 1~2 秒。缓解手段：登录事件触发**异步预热**，或返回一个「欢迎回来」的降级 Feed。

#### 2.3.6 存储估算 —— 推文本体（Tweet Storage）

```
单条推文大小估算：
  tweet_id      8 B
  author_id     8 B
  text        上限 280 字符，实际平均约 100 字符，UTF-8 ~2 B/字符 ≈ 200 B
  created_at    8 B
  media_url    ~40 B（可选）
  metadata     ~36 B（回复/引用 ID、语言、地理位置等）
  ──────────────────────────
  合计       ≈ 300 字节/条   ⭐

每天新增 = 5 亿条 × 300 字节
        = 5 × 10⁸ × 3 × 10²
        = 1.5 × 10¹¹ 字节
        = 150 GB/天   ⭐

每年新增 = 150 GB × 365
        = 54,750 GB
        ≈ 55 TB/年   ⭐

5 年累计 ≈ 275 TB（不含副本）
含 3 副本 ≈ 825 TB
```

✅ **结论：推文本体的存储量（55 TB/年）在现代分布式存储里完全不是问题** —— 一个 Cassandra / TiDB 集群几十台机器就能装下，而且可以做冷热分层：

| 数据层 | 时间范围 | 占比 | 存储介质 | 访问延迟 |
|--------|---------|------|---------|---------|
| 🔥 热 | 最近 7 天 | ~1 TB | Redis / 内存缓存 | < 1ms |
| 🟡 温 | 最近 1 年 | 55 TB | NVMe SSD（Cassandra） | ~5ms |
| ❄️ 冷 | 1 年以上 | 220 TB | S3 / 对象存储 + 归档 | ~200ms（可接受，几乎无人访问） |

> 💡 **面试中怎么说**：「注意存储的两个数量级差异 —— **推文本体 55TB/年，磁盘就能扛；Feed 索引只有 4TB，但必须放内存。** 4TB 的内存比 55TB 的 SSD 贵得多。所以真正的成本瓶颈不是数据量，而是**为了 200ms 延迟而必须把索引常驻内存**这个约束。这也解释了为什么 Feed entry 要压到 24 字节 —— 每省 1 字节，2 亿用户 × 800 条就是 160GB 内存。」

#### 2.3.7 估算结果汇总

| 指标 | 数值 | 是否瓶颈 |
|------|------|---------|
| 发推写 QPS（平均 / 峰值） | 6,000 / 50,000 | ✅ 无压力 |
| 读 Feed QPS（平均 / 峰值） | 35 万 / 100 万 | 🟡 需缓存，可解 |
| **Fan-out 写 QPS（平均 / 峰值）** | **115 万 / 900 万** | 🔥 **核心瓶颈** |
| 单条大 V 推文 fan-out 耗时 | **200 秒**（1 亿粉丝） | 🔥 **核心瓶颈** |
| Fan-out on Read 的后端查询 QPS | 7,000 万 | ❌ 直接排除该方案 |
| Feed 缓存内存（全量 / 仅活跃） | 4 TB / 1.1 TB | 🟡 成本瓶颈 |
| 推文本体存储 | 150 GB/天，55 TB/年 | ✅ 无压力 |
| 读写比 | 60 : 1 | 💡 决定计算前置到写路径 |

---

## 三、核心实体（Core Entities）

面试中在白板上先写这四个实体，**不要一上来写完整 DDL**，先写字段和职责，等到 High-Level Design 之后再补索引和分片键。

```python
# ============================================================
# 1. User —— 用户
# ============================================================
User {
    user_id:        int64      # 主键，Snowflake / 雪花 ID，全局唯一
    username:       string     # 唯一，登录名 @handle，需要唯一索引
    display_name:   string     # 展示名，可重复
    avatar_url:     string     # 头像 CDN 地址（不存二进制）
    bio:            string     # 个人简介
    created_at:     timestamp

    # ⭐ 反范式的计数字段：为了避免每次读都 COUNT(*)
    follower_count: int64      # 粉丝数，最终一致（异步更新）
    following_count:int32      # 关注数
    tweet_count:    int64      # 发推数

    # ⭐ Fan-out 策略的判定字段 —— 整个系统最关键的一个 bool
    is_celebrity:   bool       # follower_count > 100 万 → true
                               # true 时该用户的推文【不做 fan-out】，走 Pull 模式
                               # 由后台任务定期刷新，避免每次发推都查粉丝数

    last_active_at: timestamp  # ⭐ 用于「只给活跃用户 fan-out」的判定
                               # > 30 天未活跃 → 跳过 fan-out，省一半写放大
}
# 存储：MySQL / PostgreSQL 分片（按 user_id 哈希），或 DynamoDB
# 量级：2 亿行 × ~500 B ≈ 100 GB —— 很小，可全量缓存热点用户


# ============================================================
# 2. Tweet / Post —— 推文（不可变，append-only）
# ============================================================
Tweet {
    tweet_id:       int64      # ⭐ 主键，必须是 Snowflake ID
                               # 结构：[1 符号位][41 时间戳 ms][10 机器ID][12 序列号]
                               # 关键性质：单调递增 → ID 本身就是时间序
                               #          → 排序和游标分页都可以只用这一个字段
    author_id:      int64      # ⭐ 分片键：同一作者的推文落在同一分片
                               #    → User Timeline 查询变成单分区扫描
    text:           string     # 正文，≤ 280 字符
    media_urls:     list<str>  # 图片/视频的 CDN 地址，最多 4 个
    created_at:     timestamp  # 冗余存储（可从 tweet_id 解出，但显式存便于 SQL 排序）

    # 推文类型（决定 Feed 中的渲染方式）
    type:           enum       # ORIGINAL | RETWEET | QUOTE | REPLY
    parent_id:      int64      # 转发/引用/回复指向的原推 ID，ORIGINAL 时为 null

    # 反范式计数，最终一致
    like_count:     int64      # ⚠️ 高频变化，【绝不能】存进 Feed 缓存
    retweet_count:  int64      #    读时从独立的 Counter Service 拿
    reply_count:    int64

    deleted:        bool       # ⭐ 软删除：Feed 里的 tweet_id 可能指向已删推文
                               #    → 读时过滤（hydration 阶段发现为空就跳过）
                               #    → 不去 200 万份 Feed 里挨个删（那是灾难）
}
# 存储：Cassandra / DynamoDB（写多、KV 点查、天然分区）
#      分区键 author_id，聚簇键 tweet_id DESC → User Timeline 查询 O(1) 定位
# 量级：150 GB/天，55 TB/年


# ============================================================
# 3. Follow —— 关注关系（图的边）
# ============================================================
Follow {
    follower_id:    int64      # 谁关注了别人（粉丝，边的起点）
    followee_id:    int64      # 被关注者（作者，边的终点）
    created_at:     timestamp
    # 主键 = (follower_id, followee_id) 复合唯一
}
# ⭐⭐ 关键设计：这张表必须【双向存两份】（两个物理表 / 两套索引）
#
#   表 A: following  分区键 = follower_id
#         → 查询「A 关注了谁」（渲染关注列表、Pull 模式归并时用）
#         → SELECT followee_id WHERE follower_id = A
#
#   表 B: followers  分区键 = followee_id
#         → 查询「谁关注了 B」（⭐ Fan-out 时用，这是写路径的命脉）
#         → SELECT follower_id WHERE followee_id = B
#
# ⚠️ 为什么必须两份：
#    如果只按 follower_id 分片，那么「查 B 的 1 亿粉丝」要扫全部分片 → 不可行。
#    代价是写入时要双写两张表（最终一致即可，关注关系晚 1 秒同步无感知）。
#
# ⚠️ 大 V 的粉丝列表是超级大分区（1 亿行），需要二级分片：
#    分区键 = (followee_id, bucket_id)，bucket_id = follower_id % 1000
#    → 1 亿行拆成 1000 个 10 万行的分区，且天然支持并行 fan-out
#
# 量级：2 亿用户 × 200 关注 = 400 亿条边 × 2（双写）= 800 亿行
#      × 24 B ≈ 2 TB（这比推文本体还小，但行数是它的 160 倍）


# ============================================================
# 4. Feed / Timeline —— 预计算的时间线（这是【缓存】不是【数据源】）
# ============================================================
Feed {
    user_id:        int64      # ⭐ 拥有这条 Feed 的【消费者】（不是作者！）
                               #    这是 Feed 的分片键
    entries:        list<FeedEntry>   # 有序列表，按 timestamp DESC，定长 800
}

FeedEntry {                    # ⭐ 只有 24 字节，一个纯指针
    tweet_id:       int64      # 8 B —— 指向 Tweet 表
    author_id:      int64      # 8 B —— 读时快速过滤（拉黑/静音/已取关）
    timestamp:      int64      # 8 B —— 排序键（毫秒）
}
# ❌ 不存 text、不存 author_name、不存 like_count
#    原因：① 内容重复 200 万份，内存爆炸（4TB → 80TB）
#         ② 计数实时变化，无法维护 200 万份副本的一致性
#         ③ 用户信息变更（改名/换头像）要回溯改所有历史 Feed —— 不可能
#    → 统一在读路径做 hydration（水合），从各自的 Service 批量取最新值

# 物理存储：Redis Sorted Set（ZSET）
#   key   = "feed:{user_id}"
#   score = timestamp（毫秒，注意：不能用完整 Snowflake ID，见下）
#   member= "{tweet_id}:{author_id}"
#
# ⚠️ 精度陷阱（Staff 级细节）：
#   Redis ZSET 的 score 是 IEEE-754 double，整数精确范围只有 2^53。
#   Snowflake ID 是 64 位，> 2^53 → 直接当 score 会【丢失低位精度】，
#   导致同一毫秒内的多条推文排序错乱甚至去重错误。
#   ✅ 正确做法：score 用 41 位毫秒时间戳（< 2^53 安全），
#              同 score 时 Redis 按 member 字典序排 —— 所以 member 要以
#              零填充的 tweet_id 开头，保证字典序 == 数值序。
```

**Redis 侧的具体操作：**

```bash
# ── 写路径：fan-out 时向粉丝的 Feed 追加一条 ──────────────
# score = 推文毫秒时间戳，member = "tweet_id:author_id"
ZADD feed:10086 1755212345678 "1897654321098765432:2001"

# 裁剪：只保留最新 800 条（ZSET 按 score 升序，保留 rank -800 到 -1）
ZREMRANGEBYRANK feed:10086 0 -801

# 设置 TTL：30 天不活跃自动回收内存（配合「只推活跃用户」策略）
EXPIRE feed:10086 2592000

# ── 读路径：拉首屏 20 条（倒序）─────────────────────────
ZREVRANGE feed:10086 0 19 WITHSCORES

# ── 读路径：游标分页，拉「早于 cursor_ts」的 20 条 ────────
# (score 上界用开区间，避免重复返回边界那一条
ZREVRANGEBYSCORE feed:10086 (1755212345678 -inf LIMIT 0 20 WITHSCORES

# ── 批量 fan-out：必须用 Pipeline，否则 RTT 打满 ──────────
# 单条 ZADD RTT ~0.5ms → 200 个粉丝串行 = 100ms ❌
# Pipeline 100 条一批 → 200 个粉丝 = 2 次 RTT = 1ms ✅  提速 100 倍
```

> 💡 **面试中怎么说**：「我想强调 Feed 表和其它三张表的**本质区别**：User / Tweet / Follow 是 **source of truth**，丢了就是数据事故；而 Feed 是**纯粹的物化视图（materialized view）**，它 100% 可以从前三者重建。这个定位决定了：① 它可以放在易失的 Redis 里；② Redis 集群整个挂掉不是数据事故，只是降级到 Pull 模式、延迟变高；③ 我们可以随便对它做 TTL、裁剪、只给活跃用户构建 —— 因为丢了能重建。**把可重建的东西放内存、把 source of truth 放持久化存储，这是整个架构的分层原则。**」

---

## 四、API 设计（API / System Interface）

**统一约定：**

- 所有接口走 HTTPS，`Authorization: Bearer <JWT>`，`user_id` **从 token 中解析，绝不从请求体中读取**（防越权）
- 写接口带 `Idempotency-Key` 头，防止客户端重试导致重复发帖
- 时间戳统一用 **毫秒 Unix 时间戳（int64）**，不用 ISO 字符串（省带宽、免时区解析）
- 错误返回统一 `{ "error": { "code": "...", "message": "..." } }`

### 4.1 POST /v1/tweets —— 发帖

**请求：**

```http
POST /v1/tweets HTTP/1.1
Authorization: Bearer eyJhbGciOi...
Idempotency-Key: 8f14e45f-ea8f-4b1c-9c2e-3d5a7b9c1e2f
Content-Type: application/json

{
  "text": "刚刚跑完人生第一个半马，用时 1 小时 52 分 🏃",
  "media_urls": [
    "https://cdn.example.com/m/9a8b7c6d.jpg"
  ],
  "type": "ORIGINAL",
  "parent_id": null
}
```

**响应（201 Created）：**

```json
{
  "tweet_id": "1897654321098765432",
  "author_id": "2001",
  "text": "刚刚跑完人生第一个半马，用时 1 小时 52 分 🏃",
  "media_urls": ["https://cdn.example.com/m/9a8b7c6d.jpg"],
  "type": "ORIGINAL",
  "parent_id": null,
  "created_at": 1755212345678,
  "like_count": 0,
  "retweet_count": 0,
  "reply_count": 0,
  "fanout_status": "QUEUED"
}
```

**设计要点：**

| 要点 | 说明 |
|------|------|
| ⭐ **`tweet_id` 用字符串返回** | int64 超过 JavaScript `Number.MAX_SAFE_INTEGER`（2^53），前端 `JSON.parse` 会丢精度 → **必须序列化为 string**。这是极其常见的线上事故 |
| ⭐ **`fanout_status: "QUEUED"`** | 接口在**推文落库成功 + 消息投递到 Kafka 成功**后就返回，**不等 fan-out 完成**。发推 P99 因此能做到 < 500ms，而不是大 V 的 200 秒 |
| ⭐ **`Idempotency-Key`** | 弱网重试是移动端常态。服务端用该 key 在 Redis 做 `SET NX EX 86400`，重复请求直接返回首次结果，避免重复发帖 |
| ⭐ **不返回作者信息** | 客户端已经知道自己是谁，省一次 join，省带宽 |
| ⚠️ **read-your-writes** | 服务端**同步**把这条推写入作者自己的 `feed:{author_id}`（1 次 Redis 写，~0.5ms），保证作者刷新立刻可见；其余 200 个粉丝走异步 |

### 4.2 GET /v1/feed —— 拉取 Home Timeline（核心接口）

**请求：**

```http
GET /v1/feed?cursor=eyJ0cyI6MTc1NTIxMjM0NTY3OCwiaWQiOiIxODk3NjU0MzIxMDk4NzY1NDMyIn0&limit=20 HTTP/1.1
Authorization: Bearer eyJhbGciOi...
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `cursor` | string | ❌ | 上一页返回的 `next_cursor`；**首屏不传**，表示从最新开始 |
| `limit` | int | ❌ | 每页条数，默认 20，**服务端强制上限 100**（防止被拖库） |

**响应（200 OK）：**

```json
{
  "items": [
    {
      "tweet_id": "1897654321098765432",
      "text": "刚刚跑完人生第一个半马，用时 1 小时 52 分 🏃",
      "media_urls": ["https://cdn.example.com/m/9a8b7c6d.jpg"],
      "created_at": 1755212345678,
      "author": {
        "user_id": "2001",
        "username": "runner_li",
        "display_name": "跑步的老李",
        "avatar_url": "https://cdn.example.com/a/2001.jpg",
        "is_verified": true
      },
      "stats": {
        "like_count": 1523,
        "retweet_count": 88,
        "reply_count": 42
      },
      "viewer_state": {
        "liked": false,
        "retweeted": false
      }
    },
    {
      "tweet_id": "1897654320000000001",
      "text": "今晚的月亮真圆",
      "media_urls": [],
      "created_at": 1755212300000,
      "author": { "user_id": "3077", "username": "moon_watcher", "display_name": "望月", "avatar_url": "https://cdn.example.com/a/3077.jpg", "is_verified": false },
      "stats": { "like_count": 12, "retweet_count": 0, "reply_count": 3 },
      "viewer_state": { "liked": true, "retweeted": false }
    }
  ],
  "next_cursor": "eyJ0cyI6MTc1NTIxMjMwMDAwMCwiaWQiOiIxODk3NjU0MzIwMDAwMDAwMDAxIn0",
  "has_more": true
}
```

**响应结构要点：**

- `author` / `stats` / `viewer_state` 三块都是 **hydration（水合）** 阶段填充的，**不存在 Feed 缓存里**
- `stats` 从独立的 Counter Service 批量取（一次 `MGET` 20 个 key）
- `viewer_state`（我点没点赞）是 **per-viewer** 的，同一条推给不同人返回不同值 → **这也是 Feed 响应无法做 CDN 缓存的原因**

#### ⭐ 4.2.1 为什么必须用 cursor 游标分页，而不是 offset 分页

这是这个接口**最值得展开的设计点**，面试官几乎必问。

**问题一：offset 分页在数据流动时会重复 / 跳过内容**

Feed 是一个**头部持续插入新元素**的列表 —— 这跟「按 ID 排序的静态商品列表」有本质区别。

```
时刻 T0，用户拉第一页（LIMIT 20 OFFSET 0）：

  index:  0    1    2    3   ...  17   18   19   20   21   22
  推文:  [T1] [T2] [T3] [T4] ... [T18][T19][T20][T21][T22][T23]
         └────────────── 返回给用户，第 1 页 ──────────────┘
                                            ↑ 下一页应该从这里开始

────────────────────────────────────────────────────────────────

时刻 T1（用户读了 30 秒），关注的人又发了 3 条新推 N1 N2 N3
它们插在【最前面】，整个列表右移 3 位：

  index:  0    1    2    3    4    5   ...  20   21   22   23   24
  推文:  [N1] [N2] [N3] [T1] [T2] [T3] ... [T18][T19][T20][T21][T22]
                                            ↑
  用户下拉，请求 LIMIT 20 OFFSET 20
  → 返回 index 20~39 = [T18][T19][T20][T21]...

  ❌ 但 T18、T19、T20 在第一页【已经看过了】—— 用户看到重复内容！
     重复条数 = 新插入的条数 = 3 条
```

反过来，如果期间有 3 条推被**删除**（作者删推、被拉黑），列表左移，`OFFSET 20` 会**跳过** 3 条从未展示过的内容 —— 用户永久错过。

```
                    offset 分页在动态列表上的两种错误

    新增 3 条  →  列表右移  →  第 2 页重复展示 3 条   ❌ 用户体验：似曾相识
    删除 3 条  →  列表左移  →  第 2 页跳过 3 条       ❌ 用户体验：内容丢失

    错误率 = 翻页间隔内的变动条数 / 每页条数
           = 关注 200 人、每人日均 2.5 条 → 500 条/天 ≈ 每 3 分钟 1 条新推
           → 用户停留 1 分钟翻 5 页，全程新增约 0.35 条 ≈ 0.07 条/页（错误率 ≈ 1.7%）  ⚠️ 长会话累积明显
```

**问题二：offset 分页的性能是 O(N)，深翻页会崩**

```sql
-- 翻到第 50 页
SELECT * FROM feed WHERE user_id = 10086
ORDER BY created_at DESC
LIMIT 20 OFFSET 1000;
-- ⚠️ 数据库必须【扫描并丢弃】前 1000 行，才能取到要的 20 行
--    OFFSET 越大越慢，时间复杂度 O(offset)
```

Redis 同理：`ZREVRANGE feed:10086 1000 1019` 在 skiplist 上仍需从头跳跃定位，**复杂度 O(log N + M)**，虽比 SQL 好但依然随 offset 增长。

**cursor 分页的性能是 O(log N)，与页深无关：**

```bash
# 无论第 1 页还是第 50 页，都是同样的代价
ZREVRANGEBYSCORE feed:10086 (1755212300000 -inf LIMIT 0 20
#                            ↑ 直接按 score 二分定位，O(log N)
```

```sql
-- SQL 侧的等价写法（keyset pagination）
SELECT * FROM feed
WHERE user_id = 10086
  AND (created_at, tweet_id) < (1755212300000, 1897654320000000001)   -- ⭐ 行值比较
ORDER BY created_at DESC, tweet_id DESC
LIMIT 20;
-- ✅ 走 (user_id, created_at, tweet_id) 复合索引，直接 seek 到位置，O(log N)
```

**对比总结：**

| 维度 | Offset 分页 | ⭐ Cursor 分页 |
|------|------------|---------------|
| **动态列表正确性** | ❌ 新增→重复，删除→跳过 | ✅ 游标锚定具体记录，插入/删除不影响 |
| **深翻页性能** | ❌ O(offset)，第 100 页要扫 2000 行 | ✅ O(log N)，恒定 |
| **能否跳到第 N 页** | ✅ 支持 | ❌ 只能顺序翻（Feed 场景不需要） |
| **总页数展示** | ✅ 可以 | ❌ 不能（Feed 是无界流，本来也没有总数） |
| **服务端有状态** | 无 | 无（游标是客户端持有的） |
| **适用场景** | 静态、有限、需要跳页（如后台管理表格） | **无限滚动的动态流（Feed / 消息 / 日志）** ✅ |

#### ⭐ 4.2.2 游标该用什么？两种方案

**方案 A：`(timestamp, tweet_id)` 复合游标**

```python
import base64, json

def encode_cursor(timestamp: int, tweet_id: int) -> str:
    """把最后一条记录的排序键编码成不透明字符串返回给客户端"""
    payload = {"ts": timestamp, "id": str(tweet_id)}   # ⭐ id 转 string，防 JS 精度丢失
    raw = json.dumps(payload, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

def decode_cursor(cursor: str) -> tuple[int, int]:
    """解码游标，拿到 (timestamp, tweet_id) 二元组"""
    pad = "=" * (-len(cursor) % 4)                     # 补回被 strip 的 padding
    payload = json.loads(base64.urlsafe_b64decode(cursor + pad))
    return payload["ts"], int(payload["id"])
```

⚠️ **为什么必须是复合游标，而不能只用 timestamp？**

```
毫秒级时间戳会碰撞。5 亿推文/天，峰值 5 万 QPS
→ 同一毫秒内平均有 50 条推文（峰值时）

假设第 1 页最后一条是 T20，时间戳 = 1755212300000，
同一毫秒还有 T21、T22 也是 1755212300000：

  ❌ 只用 timestamp 做游标：
     WHERE created_at < 1755212300000      → T21、T22 被【永久跳过】
     WHERE created_at <= 1755212300000     → T20 被【重复返回】
     ⚠️ 无论用 < 还是 <=，都必然出错。这是个死结。

  ✅ 用 (timestamp, tweet_id) 复合游标：
     WHERE (created_at, tweet_id) < (1755212300000, 1897654320000000001)
     → 同毫秒内按 tweet_id 二次排序，边界唯一且确定，不重不漏 ✅
```

**方案 B：Snowflake ID 单字段游标（更优雅）⭐**

```
Snowflake ID 的位结构（64 bit）：
┌─┬───────────────────────────────────┬──────────┬────────────┐
│0│      41 bit 毫秒时间戳             │10 bit 机器│12 bit 序列号│
└─┴───────────────────────────────────┴──────────┴────────────┘
 符号位        可用 69 年               1024 台    每毫秒 4096 个

⭐ 关键性质：ID 高位就是时间 → ID 的数值大小顺序 == 时间顺序
          且 ID 全局唯一 → 天生就是一个「复合游标」，时间和唯一性合二为一
```

```python
# 游标就是上一页最后一条的 tweet_id，一个数字搞定
cursor = "1897654320000000001"

# SQL 侧
# SELECT * FROM feed WHERE user_id=? AND tweet_id < ? ORDER BY tweet_id DESC LIMIT 20
```

| 维度 | 方案 A `(ts, id)` 复合游标 | 方案 B Snowflake 单字段 |
|------|--------------------------|------------------------|
| 游标大小 | ~60 字节（base64 JSON） | ~19 字节（数字字符串） |
| 索引 | 需要 `(user_id, ts, id)` 复合索引 | 只需 `(user_id, tweet_id)` |
| SQL 写法 | 行值比较，部分老版本 MySQL 优化差 | 简单 `<` 比较，索引利用完美 |
| 前提条件 | 无 | 必须全系统用 Snowflake 且时钟单调 |
| ⚠️ 风险 | 无 | **时钟回拨**会破坏单调性 → 需 NTP + 回拨检测 |
| **Redis ZSET 适配** | ✅ score = ts（41 位 < 2^53，安全） | ⚠️ **score 不能直接放完整 Snowflake**（64 位 > 2^53，double 丢精度） |

> 💡 **面试中怎么说**：「我会用 **Snowflake ID 做游标**，因为它把『时间序』和『唯一性』压进了一个 int64，省掉了复合索引。但有个坑要注意：**Redis ZSET 的 score 是 double，只有 53 位整数精度**，直接把 64 位 Snowflake 当 score 会丢低位、排序错乱。所以在 Redis 层我会用 **score = 41 位毫秒时间戳，member = 零填充的 tweet_id 拼 author_id** —— 同 score 时 Redis 按 member 字典序排，零填充保证字典序等于数值序。这样 `ZREVRANGEBYSCORE` 配合客户端对边界同 score 元素做一次去重，就能做到不重不漏。」

⚠️ **额外要点：游标必须是不透明（opaque）的**
不要让客户端解析或构造游标。用 base64（甚至加签名）包一层，好处是：**将来把存储从 Redis ZSET 换成别的（比如加上大 V merge 的双游标），可以直接改游标内部结构而不破坏客户端兼容性。** 大 V 混合模式下，游标实际会变成 `{"push_ts": ..., "pull_ts": ...}` 的双游标 —— 不透明设计让这次演进零成本。

### 4.3 POST /v1/users/{id}/follow —— 关注

**请求：**

```http
POST /v1/users/2001/follow HTTP/1.1
Authorization: Bearer eyJhbGciOi...
Idempotency-Key: c3d1f0a2-77bb-4e8e-9a1d-6f0b2c4d8e10
Content-Type: application/json

{}
```

**响应（200 OK）：**

```json
{
  "follower_id": "10086",
  "followee_id": "2001",
  "following": true,
  "created_at": 1755212400000,
  "followee": {
    "username": "runner_li",
    "display_name": "跑步的老李",
    "follower_count": 15234,
    "is_celebrity": false
  },
  "backfill_status": "QUEUED"
}
```

**设计要点：**

| 要点 | 说明 |
|------|------|
| ⭐ **幂等** | 重复关注返回 200 而非 409，`following: true` 即可。移动端重复点击是常态 |
| ⭐ **双写两张表** | 同步写 `following`（分区键 follower_id）+ 异步写 `followers`（分区键 followee_id）。后者是 fan-out 的数据源，晚 1 秒无感知 |
| ⭐ **`backfill_status`（回填）** | 关注后，被关注者的**历史推文要不要塞进我的 Feed？**<br>✅ **要**，否则新关注一个人后 Feed 里长时间看不到他 → 用户以为关注失败<br>做法：异步任务拉 followee 最近 ~50 条推，`ZADD` 进 `feed:{follower_id}`，按原时间戳插入（会散落在时间线中间，不都在顶部）<br>⚠️ 但如果 followee 是大 V（`is_celebrity: true`）→ **不回填**，因为读路径本来就会实时 merge 大 V 内容 |
| ⭐ **返回 `is_celebrity`** | 让客户端知道这个关注是 Push 还是 Pull 路径，便于埋点和 debug |
| ⚠️ **关注数上限** | 硬性限制 5000（Twitter 真实规则），防止爬虫号关注 100 万人导致 Pull 路径的归并路数爆炸 |

### 4.4 DELETE /v1/users/{id}/follow —— 取关

**请求：**

```http
DELETE /v1/users/2001/follow HTTP/1.1
Authorization: Bearer eyJhbGciOi...
```

**响应（200 OK）：**

```json
{
  "follower_id": "10086",
  "followee_id": "2001",
  "following": false,
  "unfollowed_at": 1755213000000,
  "feed_cleanup": "LAZY"
}
```

**设计要点 —— 这个接口比看起来难：**

| 要点 | 说明 |
|------|------|
| ⭐ **`feed_cleanup: "LAZY"`** | **取关后，已经推进我 Feed 里的 800 条数据怎么办？**<br>❌ 立刻遍历 ZSET 删掉该作者的所有条目：`ZSCAN` 800 条 + 逐条 `ZREM`，几十次 Redis 调用，且取关是高频操作（尤其批量取关）<br>✅ **惰性过滤（Lazy Filtering）**：读路径拿到 20 条 entry 后，用 `author_id` 字段（这就是为什么 FeedEntry 里要存 author_id！）比对当前关注列表，命中则丢弃<br>💡 **这就是 24 字节里那 8 字节 `author_id` 的价值** —— 它让取关、拉黑、静音三种过滤全部变成读时的 O(1) 内存比对，而不是写时的大规模数据清理 |
| ⚠️ **惰性过滤的副作用** | 取一页 20 条，过滤掉 5 条后只剩 15 条 → 需要**超量拉取（over-fetch）**：拉 `limit × 1.5 = 30` 条再过滤，凑不够就再拉一轮 |
| ⭐ **幂等** | 取关一个没关注的人，返回 200 + `following: false`，不报错 |
| ⭐ **不是 `POST /unfollow`** | 用 `DELETE` 同一个 `/follow` 资源，符合 REST 语义，且 DELETE 天然幂等 |
| ⚠️ **`follower_count` 递减** | 走异步 Counter Service，最终一致。**绝不能**在事务里 `UPDATE users SET follower_count = follower_count - 1 WHERE user_id = 2001` —— 大 V 的这一行会成为**行锁热点**，被取关的 QPS 直接锁死整张表 |

### 4.5 API 汇总

| 接口 | 方法 | 幂等 | QPS（峰值） | 延迟 SLA | 关键设计点 |
|------|------|------|------------|---------|-----------|
| `/v1/tweets` | POST | ✅（Idempotency-Key） | 5 万 | P99 < 500ms | 写完就返回，fan-out 异步；作者自己的 Feed 同步写 |
| `/v1/feed` | GET | ✅（天然） | 100 万 | **P99 < 200ms** ⭐ | Cursor 分页；只存 ID 读时 hydrate；惰性过滤 |
| `/v1/users/{id}/follow` | POST | ✅ | ~5000 | P99 < 300ms | 双写关注表；历史推文异步回填 |
| `/v1/users/{id}/follow` | DELETE | ✅ | ~5000 | P99 < 300ms | 惰性清理，不动已推送的 Feed |

> 💡 **面试中怎么说**：「四个接口里，**只有 `GET /v1/feed` 是真正难的**，它承担了 100 万峰值 QPS 和 200ms 的 P99 约束。另外三个接口的复杂度都不在接口本身，而在**它们触发的异步副作用**：发推触发 115 万 QPS 的 fan-out 风暴，关注触发历史回填，取关触发（被我们刻意推迟到读路径的）清理。**API 设计的价值在于把这些副作用显式地标注出来** —— `fanout_status`、`backfill_status`、`feed_cleanup` 这三个字段，就是我在接口层对『这里有异步，这里最终一致』的声明。接下来我会展开高层设计，重点解决那个 115 万 QPS 的写放大和大 V 的 200 秒 fan-out 延迟。」

---

## 五、策略一：Push Model（Fan-out on Write）

### 核心思想

> 💡 **核心思想**：**发帖的时候就把 tweet_id 主动推（写入）到每一个粉丝的 Feed 缓存里，读的时候直接取现成的列表。**

这是一个典型的**用写换读**的设计：把 Feed 的计算复杂度从"读时"提前到"写时"。

在我们的场景里，读写比是 **60:1**（读 QPS 35 万 vs 写 QPS 6,000）。既然读的次数是写的 60 倍，那么把昂贵的"聚合 200 个关注对象的推文并排序"这件事，从**每天 300 亿次的读路径**上挪走，只在**每天 5 亿次的写路径**上做一次，直觉上是划算的。

| 维度 | 读时计算（Pull） | 写时计算（Push） |
|---|---|---|
| 计算发生在 | 每次读 Feed 时 | 每次发推时 |
| 频次 | 300 亿次/天 | 5 亿次/天 |
| 读延迟 | 高（需要 fan-in + 归并排序） | **极低（一次 Redis 读）** |
| 写延迟 | 极低（只写一条） | **高（要写 N 份）** |
| 存储 | 一份原始数据 | **每个用户一份 Feed 副本** |

Push 模型的本质是：**用存储空间和写放大，换取读延迟**。

面试中一句话表述：
> "既然读比写多 60 倍，我就把 Feed 预计算好（materialized view），每个用户维护一个物理上独立的收件箱（inbox），发帖时往所有粉丝的收件箱里投递。读 Feed 退化成一次 O(1) 的缓存查询。"

---

### 工作流程

#### 写路径（发帖 → 扇出）

```
                        ┌──────────────────────────────────────────┐
                        │            写路径 (Write Path)            │
                        └──────────────────────────────────────────┘

  ┌────────┐   POST /tweet   ┌─────────────┐
  │ Client │ ───────────────>│ API Server  │
  └────────┘                 └──────┬──────┘
      ▲                             │
      │                             │ ① 先落库（同步，必须成功）
      │                             ▼
      │                    ┌──────────────────┐
      │                    │   Tweet DB       │  分库分表 by tweet_id
      │                    │  (MySQL/Cassandra)│  这是唯一的 Source of Truth
      │                    └────────┬─────────┘
      │                             │
      │                             │ ② 写成功后投递事件
      │                             ▼
      │                    ┌──────────────────┐
      │  ③ 立即返回 200     │   Kafka Topic    │  topic: tweet_created
      └────────────────────│  tweet_created   │  partition by author_id
        （~50ms，不等扇出）  └────────┬─────────┘
                                     │
                                     │ ④ 异步消费
                                     ▼
                          ┌─────────────────────┐
                          │   Fanout Worker     │  水平扩展的消费者集群
                          │   (消费者组，N 实例)  │
                          └──────────┬──────────┘
                                     │
                        ⑤ 查粉丝列表  │  SELECT follower_id FROM followers
                                     │  WHERE followee_id = ? （分批 pull）
                                     ▼
                          ┌─────────────────────┐
                          │  Follower Graph DB  │
                          └──────────┬──────────┘
                                     │
                        ⑥ pipeline 批量写入    │
              ┌──────────────┬───────┴───────┬──────────────┐
              ▼              ▼               ▼              ▼
       ┌────────────┐ ┌────────────┐  ┌────────────┐ ┌────────────┐
       │ Redis 分片1 │ │ Redis 分片2 │  │ Redis 分片3 │ │ Redis 分片N │
       │ feed:{u1}  │ │ feed:{u2}  │  │ feed:{u3}  │ │ feed:{uN}  │
       │ [tid,tid..]│ │ [tid,tid..]│  │ [tid,tid..]│ │ [tid,tid..]│
       └────────────┘ └────────────┘  └────────────┘ └────────────┘
              └──────────────┴───────────────┴──────────────┘
                        每个粉丝一个 Feed（收件箱），只存 tweet_id
```

#### 读路径（拉 Feed）

```
                        ┌──────────────────────────────────────────┐
                        │            读路径 (Read Path)             │
                        └──────────────────────────────────────────┘

  ┌────────┐  GET /feed   ┌─────────────┐
  │ Client │ ────────────>│ API Server  │
  └────────┘              └──────┬──────┘
      ▲                          │
      │                          │ ① 一次 Redis 读，拿到 20 个 tweet_id
      │                          ▼
      │                 ┌──────────────────────────────────┐
      │                 │  ZREVRANGE feed:{uid} 0 19       │  ← O(log N + 20)
      │                 │  返回: [t_991, t_988, t_985, ...] │     实测 < 5ms
      │                 └──────────────┬───────────────────┘
      │                                │
      │                                │ ② 批量 hydrate（水合）推文正文
      │                                ▼
      │                 ┌──────────────────────────────────┐
      │                 │  MGET tweet:t_991 tweet:t_988 ...│  ← 一次往返
      │                 │  （Tweet Content Cache）          │     命中率 ~95%
      │                 │  miss 的少量回源 Tweet DB          │     < 10ms
      │                 └──────────────┬───────────────────┘
      │                                │
      │                                │ ③ 批量拿作者信息
      │                                ▼
      │                 ┌──────────────────────────────────┐
      │                 │  MGET user:u_1 user:u_2 ...      │  ← 一次往返
      │                 └──────────────┬───────────────────┘
      │                                │
      │  ④ 组装 JSON 返回               │
      └────────────────────────────────┘
             总耗时 P99 < 50ms，远低于 200ms 目标
```

**读路径耗时拆解（P99 预算）**：

| 步骤 | 操作 | P99 耗时 | 说明 |
|---|---|---|---|
| 1 | `ZREVRANGE feed:{uid} 0 19` | ~5ms | 一次 Redis 往返，O(log N + 20) |
| 2 | `MGET tweet:*` × 20 | ~8ms | 一次批量往返，缓存命中率 95% |
| 3 | 少量 miss 回源 DB | ~15ms | 只有 ~1 条 miss，可并行 |
| 4 | `MGET user:*` × ~18 | ~5ms | 作者去重后更少 |
| 5 | 序列化 + 网络 | ~20ms | |
| **合计** | | **~50ms** | ✅ 远低于 P99 200ms 目标 |

---

#### ⭐ 关键设计一：为什么发帖必须"先落库 + 消息队列异步扇出"

**反面教材：同步扇出**

```python
# ❌ 错误做法：在 HTTP 请求里同步扇出
def post_tweet(author_id, content):
    tweet_id = db.insert_tweet(author_id, content)      # 20ms
    followers = db.get_followers(author_id)             # 可能有 1 亿个
    for f in followers:                                  # 循环 1 亿次！
        redis.zadd(f"feed:{f}", {tweet_id: now()})      # 每次 0.1ms
    return tweet_id                                      # 用户等到天荒地老
```

同步扇出的耗时账：

| 作者类型 | 粉丝数 | 同步扇出耗时（按 10 万 writes/s 算） | 用户体验 |
|---|---|---|---|
| 普通用户 | 200 | **2ms** | ✅ 可接受 |
| 中等网红 | 10 万 | **1 秒** | ⚠️ 明显卡顿 |
| 大 V | 100 万 | **10 秒** | ❌ 请求超时 |
| 头部账号 | 1 亿 | **1000 秒 ≈ 17 分钟** | ❌❌ 完全不可用 |

**正确做法：落库 + MQ 解耦**，理由有四条，面试时要能全说出来：

| 理由 | 说明 |
|---|---|
| ✅ **发帖接口延迟稳定** | 只做「写 DB + 投 Kafka」，P99 稳定在 ~50ms，与粉丝数**完全解耦**。1 亿粉丝的账号和 200 粉丝的账号，发帖体验一模一样 |
| ✅ **削峰填谷** | 峰值写 QPS 5 万，扇出峰值 = 5 万 × 200 = **1000 万 writes/s**。Kafka 作为缓冲区，Worker 按自己的处理能力匀速消费，避免瞬时打爆 Redis |
| ✅ **失败隔离与重试** | 扇出到某个 Redis 分片失败，不应该导致「发帖失败」。推文已经落库了（Source of Truth 已确定），扇出只是派生数据，Kafka 的 offset 机制天然支持重试 |
| ✅ **优先级调度** | 可以给不同作者分配不同 Kafka topic/partition：普通用户走快速通道，大 V 走独立的慢通道，避免大 V 的扇出风暴阻塞普通用户 |

> ⚠️ **顺序不能反**：必须**先写 DB，再投 MQ**。如果先投 MQ 再写 DB，Worker 可能在 DB 写入成功前就开始扇出，读 Feed 时拿到 tweet_id 却 hydrate 不到正文，出现"幽灵推文"。
>
> 严格做法用 **Transactional Outbox 模式**：DB 事务里同时写 `tweets` 表和 `outbox` 表，由 CDC（如 Debezium）读 binlog 投递到 Kafka，保证「落库」和「投递」的原子性。面试中提到这一点是加分项。

---

#### ⭐ 关键设计二：Feed 缓存里只存 tweet_id，不存正文

这是**最高频的面试追问**，必须能用数字算清楚。

**❌ 如果 Feed 里直接存正文（denormalized）**

```
feed:{user_123} = [
  {id: 991, text: "今天天气真好，出去跑了个步...", author: "张三",
   avatar_url: "https://...", created_at: ..., media: [...]},   ← ~2KB
  {id: 988, text: "...", ...},                                   ← ~2KB
  ... 800 条
]
```

**✅ 只存 tweet_id（normalized）**

```
feed:{user_123} = [991, 988, 985, 982, ...]   ← 每个 8 bytes
```

**算一笔账（用统一场景数字）**：

| 项目 | 只存 tweet_id | 存完整正文 |
|---|---|---|
| 单条 Feed 条目大小 | 8 bytes（int64 tweet_id）<br>+ 8 bytes（ZSET score）<br>+ tweet_id 8B + author_id 8B + timestamp 8B ≈ **24 字节**（裸数据）<br>⚠️ Redis ZSET 的实际内存开销约 **64 B/条**（约 4 倍），见 §八 1.4 | 正文 280 字符 UTF-8 ≈ 560B<br>+ 作者信息 ~200B<br>+ 媒体 URL/元数据 ~1KB<br>+ 时间戳等 ≈ **~2KB** |
| 每用户 800 条 | 25B × 800 = **20 KB** | 2KB × 800 = **1.6 MB** |
| 2 亿 DAU 总量 | 20KB × 2亿 = **4 TB** | 1.6MB × 2亿 = **320 TB** |
| 算上副本（3 副本）| **12 TB** | **960 TB ≈ 1 PB** |
| **冗余倍数** | 1x | **~80–200x** |

> 💡 一条推文平均被 200 个粉丝的 Feed 引用，所以存正文相当于把每条推文物理复制 200 份 —— 这就是 **200 倍数据冗余**的来源。存 id 只复制了 8 字节的指针。

**除了空间，存正文还有三个致命问题**：

| 问题 | 说明 |
|---|---|
| ❌ **编辑无法同步** | 作者编辑推文，要去 200 个（大 V 是 1 亿个）Feed 副本里逐一改写。实际上不可能做到，只能等副本自然过期 |
| ❌ **删除无法同步** | 删帖是**合规要求**（GDPR、内容违规下架）。存 id 的方案只需删 `tweet:{id}` 这一份，读时 hydrate 不到就自动过滤掉，天然「一处删除，处处生效」 |
| ❌ **无法应用动态信息** | 点赞数、转发数、"你关注的人也赞了"这类实时变化的字段，存快照就永远是旧值。存 id 则可以在 hydrate 阶段拉最新计数 |

**Hydrate（水合）的实现**：

```python
def hydrate_feed(user_id, cursor=None, limit=20):
    # ① 从 Feed 缓存拿 tweet_id 列表（只有 id，非常轻量）
    tweet_ids = redis.zrevrangebyscore(
        f"feed:{user_id}", cursor or "+inf", "-inf", start=0, num=limit
    )

    # ② 一次 MGET 批量取正文（20 个 key 一次网络往返，不是 20 次！）
    keys = [f"tweet:{tid}" for tid in tweet_ids]
    tweets = redis.mget(keys)                       # 缓存命中率 ~95%

    # ③ 少量 miss 的回源 DB，并回填缓存
    miss_ids = [tid for tid, t in zip(tweet_ids, tweets) if t is None]
    if miss_ids:
        rows = tweet_db.batch_get(miss_ids)          # 批量查，不要循环单查
        backfill_cache(rows)                         # 回填，TTL 24h
        tweets = merge_results(tweets, rows)         # ⭐ 必须把回源结果并回原列表！
                                                     #    否则「缓存未命中」会被当成「已删除」丢掉，
                                                     #    Redis 冷启动时会大面积丢内容

    # ④ 过滤掉已删除的推文（回源 DB 也拿不到 = 真的已删除，天然生效）
    #    ⚠️ 这里会导致返回条数 < limit，需要多取一些（over-fetch 20%）
    result = [t for t in tweets if t is not None]

    # ⑤ 批量取作者信息 + 实时互动计数
    result = attach_authors(result)                  # MGET user:*
    result = attach_counters(result)                 # MGET counter:*
    return result
```

> ⚠️ **面试细节**：因为删除的推文会被过滤掉，取 20 条可能只剩 17 条。实践中 over-fetch（多取 20%–30%），或者用游标循环补齐。

---

### 数据结构设计

Feed 缓存的核心需求：
1. 按时间倒序取最新 20 条
2. 支持下拉分页（游标）
3. 保持定长，不能无限增长（我们设定上限 **800 条**，约等于用户下拉 40 页，足够覆盖 99.9% 的浏览深度）
4. 写入要快（扇出时批量写）

Redis 有两种自然的实现方式。

#### 方案 A：List（LPUSH + LTRIM）

```bash
# 扇出时：把新 tweet_id 推到列表头部
LPUSH feed:user_123 991

# 立即截断，只保留最新 800 条（O(N)，但 N 固定为 800，很快）
LTRIM feed:user_123 0 799

# 读第一页（最新 20 条）
LRANGE feed:user_123 0 19
# => [991, 988, 985, ...]

# 读第二页（offset 分页）
LRANGE feed:user_123 20 39
```

#### 方案 B：Sorted Set（ZADD，score = timestamp）

```bash
# 扇出时：score 用推文的时间戳（毫秒），member 用 tweet_id
ZADD feed:user_123 1755230400123 991

# 截断：按排名删除，只保留 score 最大的 800 个
ZREMRANGEBYRANK feed:user_123 0 -801

# 读第一页（最新 20 条，带 score 方便拿游标）
ZREVRANGE feed:user_123 0 19 WITHSCORES
# => [991, 1755230400123, 988, 1755230399001, ...]

# 读下一页：用上一页最后一条的 score 作为游标（cursor-based pagination）
# "(" 表示开区间，排除掉游标本身，避免重复
ZREVRANGEBYSCORE feed:user_123 (1755230381000 -inf LIMIT 0 20

# 取关时精确删除某人的所有推文（需要配合作者→推文的索引）
ZREM feed:user_123 991 988 985
```

#### 两者对比

| 维度 | List（LPUSH + LTRIM） | Sorted Set（ZADD） | 胜者 |
|---|---|---|---|
| **写入复杂度** | `LPUSH` O(1) | `ZADD` O(log N)，N=800 → ~10 次比较 | 🟡 List 略快，但差距可忽略 |
| **读首屏复杂度** | `LRANGE 0 19` O(20) | `ZREVRANGE 0 19` O(log N + 20) | 🟡 基本持平 |
| **分页方式** | 只能 **offset 分页**（`LRANGE 20 39`） | 支持 **游标分页**（按 score） | ✅ **ZSET** |
| **分页正确性** | ❌ 翻页期间有新推文插入，会导致**重复/漏读**（offset 漂移） | ✅ 游标固定在时间戳上，翻页结果稳定 | ✅ **ZSET** |
| **乱序插入** | ❌ 只能从头部插入。补推文（backfill 历史推文、延迟到达的消息）会插错位置 | ✅ 按 score 自动排到正确位置 | ✅ **ZSET** |
| **幂等性** | ❌ Worker 重试会 `LPUSH` 两次，Feed 里出现**重复推文** | ✅ `ZADD` 同 member 只是更新 score，**天然幂等** | ✅ **ZSET** |
| **取关清理** | ❌ `LREM` 需要 O(N) 遍历，且要逐个删 | ✅ `ZREM` 支持批量，O(M·log N) | ✅ **ZSET** |
| **截断** | `LTRIM 0 799` O(N) | `ZREMRANGEBYRANK 0 -801` O(log N + M) | 🟡 持平 |
| **内存开销**（800 条）| ~10 KB（quicklist 压缩） | ~20 KB（skiplist + dict） | ✅ List 省一半 |
| **支持非时间排序** | ❌ 只能按插入顺序 | ✅ score 可以换成**排序分数**（算法 Feed 的基础） | ✅ **ZSET** |

> ✅ **推荐：Sorted Set（ZSET）**
>
> 多花的那 10 KB/用户（2 亿 DAU × 10KB = 2TB 额外内存）完全值得，换来的是：
> 1. **游标分页**的正确性 —— 这是产品必需的（用户下拉时不能看到重复推文）
> 2. **天然幂等** —— 扇出 Worker 重试不会产生重复，这是分布式系统的刚需
> 3. **乱序插入** —— 支持关注后回填历史推文、支持延迟消息
> 4. **可演进到算法 Feed** —— 把 score 从 timestamp 换成 `w1*时间衰减 + w2*互动分 + w3*亲密度`，数据结构不用动

#### 完整的 Key 设计

```bash
# ① 用户 Feed（收件箱）—— ZSET，定长 800
#    key 里加 hash tag {}，保证同一用户的相关 key 落到同一分片
feed:{user_123}                  ZSET   member=tweet_id, score=timestamp_ms

# ② 推文内容缓存 —— String（protobuf/msgpack 序列化），TTL 24h
tweet:991                        STRING  ~2KB, TTL=86400

# ③ 用户资料缓存 —— String，TTL 1h
user:u_456                       STRING  ~500B, TTL=3600

# ④ 互动计数 —— Hash（点赞/转发/回复），无 TTL，异步刷 DB
counter:991                      HASH    {likes: 1024, rt: 88, reply: 12}

# ⑤ 活跃用户标记 —— 用于跳过僵尸用户的扇出（见「致命缺陷 3」）
active:u_456                     STRING  "1", TTL=7天，用户每次登录时刷新
```

**内存总账**：

| 数据 | 单条 | 总量 | 说明 |
|---|---|---|---|
| Feed（ZSET，800 条） | 20 KB | 20KB × 2亿 = **4 TB** | 主要开销 |
| 推文内容缓存 | 2 KB | 5亿/天 × 2KB = **1 TB** | 只缓存最近 24h |
| 用户资料缓存 | 500 B | 2亿 × 500B = **100 GB** | |
| **合计（单副本）** | | **~5 TB** | |
| **算上 3 副本** | | **~15 TB** | 约需 **240 台** 64GB Redis 实例 |

---

### 关键实现：扇出 Worker

```python
import time
from kafka import KafkaConsumer

FEED_MAX_LEN   = 800     # 每个用户 Feed 最多保留 800 条（约 40 页）
FOLLOWER_BATCH = 1000    # 粉丝列表每批拉 1000 个，避免一次性加载 1 亿到内存
PIPELINE_BATCH = 500     # Redis pipeline 每 500 个命令 flush 一次
TRIM_PROB      = 0.01    # 只有 1% 的概率触发截断，摊薄开销


def fanout_worker():
    """扇出 Worker 主循环：消费发帖事件，把 tweet_id 推给所有粉丝"""
    consumer = KafkaConsumer(
        'tweet_created',
        group_id='fanout-workers',          # 消费者组，支持水平扩展
        enable_auto_commit=False,           # 手动提交 offset，保证 at-least-once
        max_poll_records=100,
    )

    for msg in consumer:
        event = decode(msg.value)           # {tweet_id, author_id, created_at}

        # 【关键分流】大 V 不走扇出，走 Pull 模式（详见混合模型章节）
        if is_celebrity(event['author_id']):
            metrics.incr('fanout.skipped_celebrity')
            consumer.commit()               # 直接跳过，不扇出
            continue

        try:
            fanout_one_tweet(event)         # 执行扇出
            consumer.commit()               # 成功后才提交 offset
        except Exception as e:
            # 不提交 offset，Kafka 会重新投递
            # 因为 ZADD 幂等，重复消费是安全的
            log.error(f"fanout failed, will retry: {e}")
            metrics.incr('fanout.retry')


def fanout_one_tweet(event):
    """把一条推文扇出到作者的所有粉丝"""
    tweet_id   = event['tweet_id']
    author_id  = event['author_id']
    score      = event['created_at']        # 毫秒时间戳，作为 ZSET 的 score

    cursor = 0                              # 粉丝列表的分页游标
    total  = 0
    t0     = time.time()

    while True:
        # ① 分批拉粉丝，每批 1000 个
        #    绝不能 SELECT * 一次性加载 —— 1 亿粉丝会直接 OOM 打死 Worker
        followers, cursor = follower_db.scan_followers(
            followee_id=author_id, cursor=cursor, limit=FOLLOWER_BATCH
        )
        if not followers:
            break

        # ② 过滤僵尸用户：几个月不登录的人，不给他预计算 Feed
        #    实测能省掉约 50% 的写入量（见「致命缺陷 3」，§2.3.5：>30 天未活跃占 50%）
        active_followers = filter_active(followers)

        # ③ 按 Redis 分片分组，同一分片的命令走同一个 pipeline
        #    这一步是性能关键：把 1000 次网络往返压缩成 ~20 次（分片数）
        by_shard = group_by_shard(active_followers)

        for shard, uids in by_shard.items():
            pipe = shard.pipeline(transaction=False)   # 不需要事务，纯批量

            for uid in uids:
                key = f"feed:{{{uid}}}"                # hash tag 保证分片一致

                # ④ 写入 Feed：ZADD 天然幂等，重复执行只是更新 score
                pipe.zadd(key, {tweet_id: score})

                # ⑤ 概率性截断：不需要每次都 trim
                #    ZREMRANGEBYRANK 保留 score 最大的 800 个（删掉排名靠前=最旧的）
                #    -801 = 倒数第 801 个，即删除 [最旧, 倒数第801] 这一段
                if random.random() < TRIM_PROB:
                    pipe.zremrangebyrank(key, 0, -(FEED_MAX_LEN + 1))

                # ⑥ 刷新 TTL：30 天没被写过也没被读过的 Feed 自动回收
                pipe.expire(key, 30 * 86400)

            pipe.execute()                             # 一次网络往返批量发送
            total += len(uids)

        # ⑦ 限流：避免大 V 的扇出瞬间打爆 Redis
        #    令牌桶控制单个 Worker 的写入速率，给在线读请求让路
        rate_limiter.acquire(len(followers))

    elapsed = time.time() - t0
    metrics.timing('fanout.latency', elapsed)
    metrics.gauge('fanout.followers', total)
    log.info(f"tweet={tweet_id} fanout to {total} followers in {elapsed:.2f}s")
```

#### 为什么必须"分批 + 幂等"

**① 为什么要分批拉粉丝（batch pull）**

| 不分批的后果 | 分批后 |
|---|---|
| ❌ **Worker OOM**：1 亿个 int64 user_id = 800MB，加上 Python 对象开销实际 **~4GB**，一个大 V 就能打死 Worker | ✅ 内存恒定：每批 1000 个，常驻内存 < 1MB |
| ❌ **DB 长事务**：一次查 1 亿行，MySQL 连接被占死几分钟，拖垮整个 follower 库 | ✅ 每次查询 < 10ms，连接快速释放 |
| ❌ **全或无**：中途失败，前面的工作全部白做，重试要从头开始 | ✅ 游标可持久化（checkpoint），失败从断点续传 |
| ❌ **无法限流**：扇出瞬间打满 Redis 带宽，在线读请求全部超时 | ✅ 批次之间可以插入令牌桶限流，给读请求让路 |

**② 为什么必须幂等（idempotency）**

Kafka 的消费语义是 **at-least-once**（至少一次）：Worker 处理到一半崩溃、网络抖动导致 commit 失败、消费者组 rebalance —— 这些都会导致**同一条消息被重复消费**。

```
时间线：
  T0  Worker A 消费 tweet_991，开始扇出
  T1  已扇出给 5 万个粉丝
  T2  Worker A 进程 OOM 崩溃 ← offset 没有提交
  T3  消费者组 rebalance，Worker B 接管该 partition
  T4  Worker B 从 T0 的 offset 重新消费 tweet_991
  T5  Worker B 又给那 5 万个粉丝写了一遍
```

| 数据结构 | 重复执行的结果 |
|---|---|
| **List** | ❌ `LPUSH feed 991` 执行两次 → Feed 里出现**两条一模一样的推文**。用户看到重复内容，体验崩坏，且没有简单的去重手段（LREM 是 O(N)） |
| **ZSET** | ✅ `ZADD feed 1755230400123 991` 执行两次 → member `991` 已存在，只是把 score 更新为同一个值。**结果完全一致，天然幂等** |

> ⭐ **这是选 ZSET 的又一个决定性理由**：在分布式系统里，「重试」是常态而不是异常。选择一个**天然幂等**的数据结构，比在应用层写去重逻辑（额外维护一个 SET 记录已处理的 tweet_id，还要考虑过期）便宜得多。
>
> 面试中这样说：*"我选 ZSET 不只是为了排序，更重要的是它给了我 at-least-once 消费下的 exactly-once 效果 —— ZADD 是幂等操作，所以我的 Worker 可以放心地失败重试，不需要额外的去重表。"*

**③ 扇出 Worker 的容量估算**

| 指标 | 数值 | 推导 |
|---|---|---|
| 平均扇出量 | 200 次写/推文 | 平均粉丝数 200 |
| 平均扇出 QPS | **115 万 writes/s** | 6,000 写QPS × 200 |
| 峰值扇出 QPS | **1,000 万 writes/s** | 5 万写QPS × 200 |
| 单 Redis 实例吞吐 | ~10 万 ops/s（pipeline 下可到 50 万） | |
| 需要 Redis 分片数 | **~100–200 个**（按峰值 + 余量） | 1000万 / 10万 = 100，再留 1~2 倍余量 |
| 单 Worker 处理能力 | ~2 万 writes/s | 受限于网络往返和 GIL |
| 需要 Worker 实例数 | **~60 个**（平均）/ **~500 个**（峰值） | 115万 / 2万 |

---

### 优点

| 优点 | 具体数字 / 说明 |
|---|---|
| ✅ **读延迟极低** | 读 Feed = **1 次 Redis ZREVRANGE**，O(log N + 20)，实测 **< 5ms**；加上 hydrate 和序列化，端到端 **P99 ~50ms**，相比 200ms 的目标有 4 倍余量 |
| ✅ **读路径极简** | 没有 fan-in、没有归并排序、没有跨 200 个数据源的并发查询。**一次缓存查询搞定**，代码几十行，几乎不会出 bug |
| ✅ **读侧可预测、可水平扩展** | 读延迟与用户关注数**完全无关** —— 关注 10 个人和关注 5000 个人，读 Feed 的耗时一模一样。容量规划非常线性 |
| ✅ **CPU 消耗在写侧，可削峰** | 扇出是异步的，Kafka 天然是缓冲区。峰值 1000 万 writes/s 可以摊到几秒内消化，**不影响在线读请求的 SLA** |
| ✅ **易于加排序 / 过滤 / 个性化** | Feed 已经物化（materialized），可以在写入时就做：屏蔽名单过滤、内容分级、去重（同一推文被多人转发只留一条）、把 score 换成排序分数实现算法 Feed |
| ✅ **读侧成本极低** | 300 亿次/天的读，如果走 Pull 需要 300亿 × 200 = 6 万亿次查询/天；Push 只需 300 亿次 Redis 读。**读侧计算量降低 200 倍** |
| ✅ **故障降级简单** | Feed 缓存挂了，可以临时降级到 Pull 模式（慢但可用），不会全站不可用 |

---

### 致命缺陷（面试核心考点）

#### 1. 写放大（Write Amplification）

一条推文的写入，会被放大成 N 次（N = 粉丝数）。

```
        1 次用户操作                        N 次系统写入
    ┌──────────────────┐            ┌────────────────────────┐
    │  用户发一条推文    │  ────>     │  写入 200 个粉丝的 Feed │
    │   (1 write)      │  放大 200x  │      (200 writes)      │
    └──────────────────┘            └────────────────────────┘
```

**量化**：

| 指标 | 计算 | 结果 |
|---|---|---|
| 平均写 QPS（用户视角） | 5 亿/天 ÷ 86400 | **6,000 QPS** |
| 平均扇出写 QPS（系统视角） | 6,000 × 200 | **115 万 QPS** |
| 峰值写 QPS（用户视角） | — | **5 万 QPS** |
| 峰值扇出写 QPS（系统视角） | 5 万 × 200 | **1,000 万 QPS** |
| 每天总扇出写次数 | 5 亿 × 200 | **1,000 亿次/天** |

> ⚠️ 也就是说，**用户看到的 6,000 QPS 写入，在系统内部是 115 万 QPS 的真实写入压力**。这个数量级已经超过了绝大多数数据库的承载能力，必须用 Redis 集群 + pipeline + 大规模水平分片才扛得住。写侧的机器成本可能是读侧的数倍。

**长尾分布让情况更糟**：平均粉丝数 200 是被长尾拉低的，实际分布高度倾斜：

| 用户分层 | 粉丝数 | 账号数占比 | 贡献的扇出量占比 |
|---|---|---|---|
| 普通用户 | < 1,000 | ~95% | ~25% |
| 中腰部 | 1,000 – 100 万 | ~5% | ~35% |
| **大 V** | **> 100 万（1 万个账号）** | **< 0.01%** | **~40%** |

**0.01% 的账号贡献了 40% 的扇出压力** —— 这就引出了下一个问题。

---

#### 2. ⭐ Celebrity Problem / Hot Key Problem（大 V 问题）

这是 Push 模型的**致命伤**，也是 Feed 系统面试的**必考题**。

**场景**：一个粉丝 1 亿的头部账号发了一条推文。

```
        ┌──────────────────────────────────────────────────────────┐
        │   @celebrity (1 亿粉丝) 发了一条推文：「大家好」           │
        └───────────────────────────┬──────────────────────────────┘
                                    │
                                    ▼
        ┌──────────────────────────────────────────────────────────┐
        │           Fanout Worker 需要执行 1 亿次 ZADD              │
        │           按 10 万 writes/s 的处理能力：                   │
        │           100,000,000 / 100,000 = 1,000 秒 ≈ 17 分钟      │
        └───────────────────────────┬──────────────────────────────┘
                                    │
                                    ▼
   扇出进度时间轴：
   ┌────────┬────────┬────────┬────────┬────────┬────────┬────────┐
   0min     3min     6min     9min     12min    15min    17min
   │        │        │        │        │        │        │
   ▼        ▼        ▼        ▼        ▼        ▼        ▼
  第1批    第2000万  第4000万  第6000万  第8000万  第9500万  第1亿
  粉丝     粉丝      粉丝      粉丝      粉丝      粉丝      粉丝
  已可见   已可见    已可见    已可见    已可见    已可见    已可见
   │                                                        │
   └────────────────── 17 分钟的可见性窗口 ───────────────────┘
        运气好的粉丝立刻看到，运气差的 17 分钟后才看到
```

**量化不一致性**：

| 粉丝所处扇出批次 | 扇出完成时刻 | 相对第一批的延迟 | 用户感知 |
|---|---|---|---|
| 第 1–100 万（前 1%） | T+10s | 10 秒 | ✅ 正常 |
| 第 1000 万（前 10%） | T+100s | 1 分 40 秒 | 🟡 有点慢 |
| 第 5000 万（前 50%） | T+500s | 8 分 20 秒 | ❌ 朋友已经在群里讨论了，我还没看到 |
| 第 1 亿（最后 1%） | T+1000s | **16 分 40 秒** | ❌❌ 严重滞后 |

**四个具体危害**：

**① Feed 不一致（Inconsistent Visibility）**

同一条推文，不同粉丝看到的时间相差最多 17 分钟。对于突发新闻、体育赛事直播、限时抢购这类场景，**这是产品级的失败**。用户会在别的渠道（群聊、其他 App）先看到消息，回到你的 App 却刷不出来。

**② ⭐ 推文顺序错乱（Out-of-Order）**

这是比"慢"更严重的问题。假设大 V 连发两条推文：

```
   T=0s     大 V 发推文 A（"我要宣布一件事"）→ 开始扇出，耗时 1000s
   T=30s    大 V 发推文 B（"就是这件事：xxx"）→ 开始扇出，耗时 1000s

   两个扇出任务并发执行，抵达不同粉丝的顺序是随机的：

   ┌──────────┬─────────────────┬─────────────────┬──────────────┐
   │  粉丝    │  收到 A 的时刻   │  收到 B 的时刻   │  Feed 展示    │
   ├──────────┼─────────────────┼─────────────────┼──────────────┤
   │ 粉丝甲   │  T+5s           │  T+35s          │ ✅ A 在前     │
   │ 粉丝乙   │  T+900s         │  T+120s         │ ❌ B 在前！   │
   │ 粉丝丙   │  T+600s         │  T+980s         │ ✅ A 在前     │
   │ 粉丝丁   │  T+950s         │  T+200s         │ ❌ B 在前！   │
   └──────────┴─────────────────┴─────────────────┴──────────────┘

   粉丝乙看到的是："就是这件事：xxx" 然后才是 "我要宣布一件事"
   —— 逻辑上完全颠倒，用户一脸问号
```

> ✅ **ZSET 部分缓解了这个问题**：因为 score 用的是推文的**创建时间戳**（而不是写入 Feed 的时间），即使 B 先写入、A 后写入，`ZREVRANGE` 读出来 A 依然排在 B 前面。
>
> ❌ **但没有完全解决**：在 A 还没扇出到粉丝乙的那段时间里（T+120s 到 T+900s），粉丝乙的 Feed 里**只有 B 没有 A**，会看到一条上下文缺失的推文。而且当 A 姗姗来迟插入到 Feed 中间位置时，用户如果已经翻过那一页，就**永远不会看到 A** 了（"消失的推文"）。
>
> 这也再次说明：**用 List（只能头部插入）会更糟** —— B 先到就永远排在 A 前面，顺序永久错乱。

**③ 扇出风暴打爆 Redis 和消息队列**

```
   正常流量：              大 V 发推：
   ┌──────────┐          ┌────────────────────────────────────┐
   │ 115万 QPS │          │  115万 QPS  +  1000万 QPS 突发洪峰  │
   │  平稳     │   ───>   │            ▲▲▲▲▲▲▲▲                │
   └──────────┘          │       ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲              │
                         │  ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁       │
                         └────────────────────────────────────┘
                              Redis CPU 打满 → 在线读请求排队
                              → 读 Feed P99 从 50ms 飙到 2000ms
                              → 触发上游超时重试 → 雪崩
```

| 受害组件 | 具体影响 |
|---|---|
| **Redis 集群** | 写入 QPS 瞬间涨 8 倍，CPU 打满，在线读请求排队，读 P99 从 50ms 飙到秒级，直接击穿 200ms SLA |
| **Kafka** | 单个 partition（按 author_id 分区）积压 1 亿条待处理，consumer lag 暴涨，**阻塞该 partition 上其他所有作者的推文扇出**（队头阻塞 Head-of-Line Blocking）—— 普通用户的推文被大 V 拖累，几分钟发不出去 |
| **Follower DB** | 分批扫描 1 亿粉丝，10 万次查询在几分钟内打到同一张表的同一个分片上，形成**热点分片** |
| **网络带宽** | 1 亿个 tweet_id + key，即使每个 100 字节也是 **10 GB** 的网络流量集中在几分钟内 |

**④ 多个大 V 同时发推 = 系统崩溃**

我们有 **1 万个大 V 账号**。把全站 1000 亿次/天的扇出量按账号类型拆开（口径与 §2.3.4 一致）：

| 指标 | 计算 | 结果 |
|---|---|---|
| 大 V 每天总发帖 | 1万个 × 2 条 | 2 万条 |
| 大 V 贡献的扇出量 | 2万 × 200万粉丝 | **400 亿次写/天** |
| 折算平均 QPS | 400亿 / 86400 | **46 万 QPS** |
| 占全站扇出量比例 | 400亿 / 1000亿 | **40%** ⭐ |

> ❌ **0.004% 的推文（2 万条 / 5 亿条）贡献了 40% 的扇出量。** 但真正致命的不是这个平均值，而是它的**时间分布**：这 400 亿次写入不是均匀摊开在 86400 秒里的，而是集中在少数几个瞬间 —— 一个 1 亿粉丝的账号发一条推，就是 1 亿次写入要在几秒内涌进来。世界杯决赛、明星官宣时多个大 V 同时发推，扇出洪峰会瞬间冲到**数千万 QPS**，是常态值（115 万）的几十倍。
>
> **纯 Push 模型在这个规模下是不可能工作的。** 这就是为什么所有真实的大规模 Feed 系统（Twitter、Instagram、微博）都必须走**混合模型**。

**面试中怎么说这一段**：
> *"Push 模型的写放大与粉丝数成正比，而社交网络的粉丝数分布是幂律的。头部 1 万个账号（占比 0.01%）就能产生 1000 万+ QPS 的扇出洪峰，一条推文的扇出要 17 分钟，这会造成可见性不一致、推文顺序错乱、以及 Kafka partition 的队头阻塞。所以我会对大 V 单独处理 —— 大 V 的推文不扇出，改成读时拉取，这就是混合模型。"*

---

#### 3. 僵尸用户浪费（Zombie User Waste）

Push 是**预计算**，但预计算的前提是"算出来的东西会被用到"。对于长期不登录的用户，这些计算和存储是**纯浪费**。

**估算（口径同 §2.3.5：> 30 天未登录的用户占 50%）**：

| 项目 | 计算 | 浪费量 |
|---|---|---|
| 浪费的写入 QPS | 115 万 × 50% | **58 万 QPS** |
| 浪费的每日写入次数 | 1,000 亿 × 50% | **500 亿次/天** |
| 浪费的存储 | 4 TB × 50% | **2 TB**（× 3 副本 = 6 TB） |
| 浪费的机器成本 | 按 Redis 集群 240 台估算 | **~120 台服务器纯浪费** |

而且实际情况更糟：真实社交产品的**注册用户 : DAU 通常是 5:1 甚至 10:1**。如果按注册用户扇出，浪费比例会高达 **80%–90%**。

**优化手段**：

```python
def filter_active(follower_ids):
    """过滤掉不活跃用户，只给活跃用户扇出"""
    # 用 Redis 的 active 标记批量判断（用户每次登录时 SET active:{uid} 1 EX 604800）
    keys = [f"active:{uid}" for uid in follower_ids]
    flags = redis.mget(keys)
    return [uid for uid, flag in zip(follower_ids, flags) if flag]
```

| 手段 | 说明 | 效果 |
|---|---|---|
| ✅ **活跃度标记** | 用户登录时打标记 `SET active:{uid} 1 EX 7d`，扇出前批量过滤 | 减少 ~50% 写入（§2.3.5 口径） |
| ✅ **Bloom Filter** | 用 Bloom Filter 存活跃用户集合，本地判断零网络开销（允许少量假阳性，多写一点无害） | 减少过滤本身的开销 |
| ✅ **Feed TTL** | 给 Feed key 设 30 天 TTL，用户不回来就自动回收内存 | 减少 ~50% 存储 |
| ✅ **回归时按需重建** | 僵尸用户重新登录时，走一次 Pull 模式实时聚合 Feed，然后重新加入扇出白名单 | 冷启动延迟 ~500ms，可接受 |

> 💡 **面试加分点**：*"我会用一个 7 天滑动窗口的活跃标记来做扇出过滤，把写入量降低 30%。用户回归时通过一次 Pull 模式的实时聚合来冷启动 Feed，代价是首次加载慢 500ms，但换来了 30% 的常态成本节约 —— 这是个很划算的交易。"*

---

#### 4. 关注 / 取关的处理难题

Push 把 Feed 变成**物化视图**，而"关注关系"一旦变化，这个物化视图就失效了：

- **关注 → 需要回填（Backfill）**：不回填的话，我关注的人在他下次发推之前，一条内容都不会出现在我的 Feed 里 —— 新用户关注 200 人后 Feed 是空的，冷启动体验灾难。而全量回填的代价是 `200 人 × 3,000 条 = 60 万次 ZADD ≈ 6 秒`，关注接口必然超时。
- **取关 / 拉黑 → 需要清理**：但 `feed:{uid}` 的 member 里**只有 `tweet_id`，拿不到 `author_id`** —— 写时清理必须把 800 条全量扫出来反查作者，再逐个 `ZREM`。
- 更糟的是这套成本会被**放大成攻击面**：一次关注 / 取关循环就是几千次 Redis 写，机器人刷一刷就是 DoS。

> 💡 **生产做法是把两侧成本都从写路径挪走**：关注走「异步限流回填最近 N 条」，取关**完全不清理**、改为读时按当前关注列表过滤 —— 写侧成本压到 O(1)。
>
> 📖 完整的方案对比、成本量化，以及可迁移的「可见性变更统一在读路径处理」原则（取关 / 拉黑 / 删帖 / 设私密 / 封号全部同构），见 **八、§2《关注 / 取关时 Feed 怎么处理（Backfill 问题）》**。

---

#### 5. 存储成本：每个用户一份物理副本

Push 模型的本质是**数据去规范化（denormalization）**：同一条推文的引用，被物理复制了 N 份。

| 存储方式 | 数据量 | 说明 |
|---|---|---|
| **原始推文数据**（Source of Truth） | 5亿/天 × 300B × 365 天 = **55 TB/年** | 存在 Cassandra/S3，冷热分离，成本低 |
| **Feed 物化视图**（Push 产生） | 2亿 DAU × 20KB = **4 TB** | 必须放在**内存（Redis）**里，单价是磁盘的 **50–100 倍** |
| Feed 副本（3 副本） | **12 TB 内存** | ~240 台 64GB Redis 实例 |
| **推文内容缓存** | 1 TB × 3 = **3 TB 内存** | 只缓存最近 24h 的热数据 |

**成本对比**：

| 项目 | Push 模型 | Pull 模型 | 差异 |
|---|---|---|---|
| Redis 内存 | ~15 TB | ~2 TB（只缓存推文本身） | **7.5x** |
| Redis 实例数 | ~240 台 | ~32 台 | **7.5x** |
| 写入 QPS | 115 万（峰值 1000 万） | 6,000 | **200x** |
| 读时计算 | 几乎为零 | 每次读 fan-in 200 个数据源 | Push 完胜 |

> 💡 **关键权衡**：Push 用 **7.5 倍的内存成本 + 200 倍的写入成本**，换来了 **200 倍的读侧计算量降低**和 **10 倍的读延迟改善**。在读写比 60:1 的场景下，这笔交易在**普通用户**身上是划算的，但在**大 V**身上是灾难性的。
>
> 这就直接推导出了混合模型的设计。

---

### 适用场景

#### ✅ 什么时候该用 Push

| 场景 | 原因 | 真实案例 |
|---|---|---|
| ✅ **粉丝数有硬上限的产品** | 扇出量可预测，不会有 Celebrity Problem | **微信朋友圈**（好友上限 5000）、**Facebook 早期**（好友上限 5000） |
| ✅ **好友关系是双向的（对称图）** | 双向关注天然限制了扇出规模，不会出现 1 亿粉丝 | 朋友圈、QQ 空间、领英动态 |
| ✅ **企业内网 / 协作工具** | 用户总数以万计，群组成员上限明确 | **Slack 频道消息**、**钉钉工作圈**、Workplace |
| ✅ **读写比极高（> 50:1）** | 读多写少，把成本转移到写侧才划算 | 我们的场景（60:1）在普通用户段成立 |
| ✅ **对读延迟要求极致（< 50ms）** | Push 是唯一能稳定做到个位数毫秒 Feed 读取的方案 | 首屏加载、无限滚动 |
| ✅ **Instagram 早期** | 用户规模和粉丝分布还没出现极端长尾时，纯 Push 完全够用 | Instagram 在 2012 年前是纯 Push |
| ✅ **通知系统 / 站内信** | 收件人明确且数量有限，语义上就是"投递" | 点赞通知、@提醒、系统公告（小范围） |

#### ❌ 什么时候不该用 Push

| 场景 | 原因 | 后果 |
|---|---|---|
| ❌ **存在超级大 V（粉丝 > 100 万）** | 扇出 17 分钟、顺序错乱、Kafka 队头阻塞 | 我们场景里有 1 万个大 V，**纯 Push 直接出局** |
| ❌ **单向关注 + 幂律分布的社交图** | 粉丝数无上限，长尾极端倾斜 | Twitter/X、微博、抖音 |
| ❌ **写多读少 或 读写比接近 1:1** | 预计算的成果用不上几次，纯浪费 | 私密日记类产品、归档系统 |
| ❌ **不活跃用户占比高（> 50%）** | 大量预计算和存储被浪费 | 注册用户 10 亿但 DAU 只有 1 亿的产品 |
| ❌ **Feed 排序规则频繁变化** | 排序逻辑一改，所有已物化的 Feed 都要重算（2 亿 × 800 = 1600 亿条记录） | 算法团队每周调模型的推荐 Feed |
| ❌ **关注关系变动极频繁** | 每次关注/取关都要回填/清理，写放大失控 | 电商"猜你喜欢"这类动态兴趣图谱 |
| ❌ **内存预算有限** | 需要 15 TB 内存 ≈ 240 台服务器 | 创业早期、成本敏感场景 |

---

### 小结

> 💡 **一句话总结**：Push 模型（Fan-out on Write）通过在发帖时把 tweet_id 预先推送到每个粉丝的 Redis Feed（推荐 ZSET，因为**支持游标分页 + 天然幂等 + 可演进到算法排序**），把读 Feed 变成一次 O(1) 的缓存查询，P99 可稳定在 50ms。代价是 **200 倍的写放大**（115 万 QPS，峰值 1000 万）、**15 TB 的内存成本**、以及无法回避的 **Celebrity Problem** —— 1 亿粉丝的账号一条推文要扇出 17 分钟，造成可见性不一致和推文顺序错乱。

**面试中的标准表述**：
> *"我会先讲纯 Push 模型：读写比 60:1，所以把计算前移到写侧是对的方向，读 Feed 退化成一次 ZREVRANGE，P99 50ms 轻松达标。Feed 里只存 tweet_id 不存正文，因为一条推文平均被 200 个 Feed 引用，存正文会带来 200 倍冗余（4TB vs 320TB），而且编辑和删除无法同步。扇出走 Kafka 异步化，保证发帖接口延迟与粉丝数解耦。*
>
> *但纯 Push 在我们的规模下会崩：写放大到 115 万 QPS，峰值 1000 万；更致命的是 1 万个大 V —— 粉丝 1 亿的账号一条推文需要 17 分钟才能扇出完，造成可见性不一致和 Kafka 的队头阻塞。所以我需要对大 V 做特殊处理，这就引出了 Pull 模型和最终的混合方案。"*

| Push 模型评分卡 | 评价 |
|---|---|
| 读延迟 | ⭐⭐⭐⭐⭐ P99 ~50ms |
| 写延迟（用户感知） | ⭐⭐⭐⭐⭐ ~50ms（异步扇出后） |
| 写入吞吐压力 | ⭐ 115 万 QPS，峰值 1000 万 |
| 存储成本 | ⭐⭐ 15 TB 内存 |
| 大 V 场景 | ❌ 完全不可用（17 分钟扇出） |
| 实现复杂度 | ⭐⭐⭐⭐ 读路径极简，写路径中等 |
| 数据一致性 | ⭐⭐ 最终一致，大 V 场景下窗口达 17 分钟 |

---

## 六、策略二：Pull Model（Fan-out on Read）

### 核心思想

> 💡 **一句话点破**：**发帖时只写自己的 User Timeline（一次写入）；读 Feed 时才现场去拉取所有关注对象的最新帖子，在内存中归并排序后返回 Top 20。**

Pull Model 是 Push Model 的镜像：它把系统的复杂度**从"写"转移到了"读"**。

| 维度 | Push（Fan-out on Write） | **Pull（Fan-out on Read）** |
|------|--------------------------|------------------------------|
| 扇出发生在 | 发帖时 | **读 Feed 时** |
| 写一条推文的成本 | 平均 200 次写、大 V 1 亿次写 | **恒定 1 次写** |
| 读一次 Feed 的成本 | 1 次 Redis `ZREVRANGE` | **200 次并发查询 + 内存归并** |
| 数据副本数 | 每条推文被复制 200 份 | **全局只有 1 份** |
| 谁承受压力 | 写路径 / 异步队列 | **读路径 / 在线请求链路** |
| 一致性 | 最终一致（秒级延迟） | **强一致（读到的永远最新）** |

**Pull 的本质是"惰性计算（Lazy Evaluation）"**：既然一条推文可能永远没人看（大量僵尸粉丝），那就别提前算，等真有人来读的时候再算。

---

### 工作流程

#### 写路径：极简，毫秒级完成

```
┌────────┐   POST /tweet   ┌──────────────┐
│ Client │ ───────────────▶│  API Server  │
└────────┘                 └──────┬───────┘
                                  │ 1. 生成 tweet_id (Snowflake，自带时间序)
                                  │ 2. 写入 Tweet 表（正文）
                                  │ 3. 写入 User Timeline 索引（作者维度）
                                  ▼
                         ┌──────────────────────┐
                         │   Tweet DB (Sharded) │
                         │   by author_id       │
                         │  ┌────────────────┐  │
                         │  │ user_timeline  │  │  ← 只写 1 行！
                         │  │ (author_id,    │  │
                         │  │  tweet_id,ts)  │  │
                         │  └────────────────┘  │
                         └──────────┬───────────┘
                                    │
                                    ▼
                            返回 200 OK（P99 < 20ms）

⭐ 无论你是 0 粉丝的新号，还是 1 亿粉丝的头部大 V，写入成本完全相同 = 1 行。
   写 QPS：平均 6,000 / 峰值 5 万 —— 单个 MySQL 集群就能扛。
```

#### 读路径：所有复杂度都在这里

```
┌────────┐  GET /feed?cursor=xxx  ┌────────────────┐
│ Client │ ──────────────────────▶│  Feed Service  │
└────────┘                        └───────┬────────┘
                                          │
                    ① 查关注列表（200 人）  │  Redis: SMEMBERS following:{uid}
                                          │  耗时 ~1ms（命中缓存）
                                          ▼
                    ┌─────────────────────────────────────────────┐
                    │  ② 并发（Scatter）拉取 200 个作者的最新 20 条  │
                    │                                              │
                    │   ┌────┐ ┌────┐ ┌────┐        ┌────┐        │
                    │   │ A1 │ │ A2 │ │ A3 │  ...   │A200│        │
                    │   └─┬──┘ └─┬──┘ └─┬──┘        └─┬──┘        │
                    │     │      │      │             │           │
                    │     ▼      ▼      ▼             ▼           │
                    │   [20条] [20条] [20条]  ...   [20条]         │
                    │                                              │
                    │   耗时 = max(200 路)，被最慢的一路决定 ⚠️      │
                    └───────────────────┬──────────────────────────┘
                                        │  共 200×20 = 4000 条候选
                                        ▼
                    ┌─────────────────────────────────────────────┐
                    │  ③ 内存 N 路归并（Gather）                    │
                    │     最小堆 / 最大堆，按 tweet_id 降序          │
                    │     ┌───┐                                    │
                    │     │ ▲ │  heapq，堆大小 K = 200              │
                    │     └───┘  只 pop 20 次即可停止 ⭐            │
                    │     耗时 ~0.3ms（纯 CPU，可忽略）             │
                    └───────────────────┬──────────────────────────┘
                                        │  Top 20 个 tweet_id
                                        ▼
                    ┌─────────────────────────────────────────────┐
                    │  ④ Hydrate 水合：批量取正文 + 作者信息 + 计数  │
                    │     MGET tweet:{id} × 20  →  ~3ms            │
                    └───────────────────┬──────────────────────────┘
                                        ▼
                              返回 20 条 Feed + next_cursor

⚠️ 一次用户可感知的"下拉刷新" = 1 次关注列表查询 + 200 次并发查询 + 1 次归并 + 1 次批量 hydrate
```

---

### 关键实现：N 路归并（K-way Merge）

每个作者的 User Timeline 本身**已经是按时间有序**的（tweet_id 用 Snowflake，高位是时间戳，天然有序）。所以问题退化成经典的 **"合并 K 个有序链表"（LeetCode 23）**。

#### Python 伪代码

```python
import heapq
from concurrent.futures import (ThreadPoolExecutor, as_completed,
                                TimeoutError as FuturesTimeout)

PAGE_SIZE = 20          # 首屏返回 20 条
FANOUT_TIMEOUT_MS = 50  # 单路拉取超时，超时即降级丢弃


def get_home_timeline(user_id: int, cursor: int | None = None) -> dict:
    """Pull 模型读时扇出：现场归并生成 Home Timeline"""

    # ---------- ① 拿到关注列表（平均 200 人） ----------
    followees = follow_cache.smembers(f"following:{user_id}")   # Redis SET，~1ms

    # 【优化 A】只保留"活跃作者"：过滤掉 30 天没发过帖的人
    # 实测 200 个关注里通常只有 30~50 人近期活跃 → 扇出直接砍掉 75%
    followees = [uid for uid in followees if active_author_bloom.might_contain(uid)]

    # 游标：None 表示首屏；否则是上一页最后一条的 tweet_id（Snowflake 自带时序）
    cursor = cursor or MAX_TWEET_ID          # 无游标时用最大值，等价于"从最新开始"

    # ---------- ② 并发扇出拉取，每路只取 PAGE_SIZE 条 ----------
    def fetch_one(author_id: int) -> list[tuple[int, int]]:
        """拉取单个作者在 cursor 之前的最新 20 条，返回 [(tweet_id, author_id), ...]"""
        # 优先打 Redis 上的 User Timeline 缓存（可共享缓存，命中率 > 95%）
        rows = user_timeline_cache.zrevrangebyscore(
            f"ut:{author_id}", max=cursor - 1, min="-inf", start=0, num=PAGE_SIZE
        )
        if rows is None:                     # 缓存未命中 → 回源 DB，并异步回填
            rows = tweet_db.query(
                "SELECT tweet_id FROM user_timeline "
                "WHERE author_id=%s AND tweet_id < %s "
                "ORDER BY tweet_id DESC LIMIT %s", (author_id, cursor, PAGE_SIZE))
            user_timeline_cache.backfill_async(author_id, rows)
        return [(tid, author_id) for tid in rows]

    lists: list[list] = []
    # ⚠️ 刻意不用 with：ThreadPoolExecutor.__exit__ 会 shutdown(wait=True)，
    #    即使超时放弃了慢的那一路，函数仍会阻塞等它跑完 —— 超时降级形同虚设
    pool = ThreadPoolExecutor(max_workers=64)
    try:
        futures = [pool.submit(fetch_one, a) for a in followees]
        try:
            # 【优化 B】全局硬超时：as_completed 的 timeout 是"整轮迭代"的总预算，
            #   而不是每一路各给 50ms（后者串行等待最坏累积成 K × 50ms = 10s）
            for fut in as_completed(futures, timeout=FANOUT_TIMEOUT_MS / 1000):
                lists.append(fut.result())
        except FuturesTimeout:                        # concurrent.futures.TimeoutError
            # 到点还没返回的路全部丢弃：宁可少几条，也不能卡死整个请求
            metrics.gauge("feed.fanout.degraded", len(futures) - len(lists))
    finally:
        # ⭐ wait=False 才是超时降级真正生效的关键；cancel_futures 取消还没排上队的任务
        pool.shutdown(wait=False, cancel_futures=True)

    # ---------- ③ N 路归并：用最大堆取 Top 20 ----------
    heap = []
    for idx, lst in enumerate(lists):
        if lst:                                       # 只把每路的"头元素"入堆
            tid, aid = lst[0]
            # Python heapq 是最小堆，取负号模拟最大堆（要最新的 = tweet_id 最大的）
            heapq.heappush(heap, (-tid, aid, idx, 0))  # (排序键, 作者, 第几路, 路内下标)

    result = []
    while heap and len(result) < PAGE_SIZE:           # ⭐ 只 pop 20 次就退出
        neg_tid, aid, idx, pos = heapq.heappop(heap)  # 弹出全局最新的一条 O(log K)
        result.append((-neg_tid, aid))

        nxt = pos + 1                                 # 该路的下一条候选补位
        if nxt < len(lists[idx]):
            tid2, aid2 = lists[idx][nxt]
            heapq.heappush(heap, (-tid2, aid2, idx, nxt))   # O(log K)

    # ---------- ④ Hydrate：批量补正文，避免 N+1 查询 ----------
    tweet_ids = [tid for tid, _ in result]
    tweets = tweet_store.mget(tweet_ids)              # 一次 MGET 20 个 key

    next_cursor = result[-1][0] if result else None   # 下一页游标 = 本页最后一条 id
    return {"tweets": tweets, "next_cursor": next_cursor}
```

#### 复杂度分析

设 K = 关注数 = 200，每路取 m = 20 条，总候选 N = K × m = 4000。

| 步骤 | 复杂度 | 实际开销（K=200） | 说明 |
|------|--------|-------------------|------|
| 建堆（每路头元素入堆） | O(K) | 200 次操作 | 或用 `heapify` 一次 O(K) |
| 每次 pop + push | O(log K) | log₂200 ≈ 7.6 | 一次比较交换 |
| 取 Top 20 | O(K + P·log K) | 200 + 20×7.6 ≈ **350 次操作** | P = 20 |
| **朴素做法（全排序）** | O(N·log N) | 4000 × 12 ≈ **48,000 次操作** | 慢 **137 倍** |
| 内存中归并总耗时 | — | **< 0.5ms** | ⭐ CPU 完全不是瓶颈 |

> 💡 **面试关键结论**：归并本身（O(N log K)）根本不是瓶颈，**瓶颈 100% 在那 200 次网络 I/O 上**。面试时如果面试官问"归并会不会很慢"，你应该反问式地纠正："归并只有 0.3ms，真正的问题是扇出的 200 次 RPC 和它带来的尾延迟放大。"

#### ⭐ 为什么"每个人只取前 20 条"就够了？

这是 Pull 模型最漂亮的剪枝优化，面试中一定要主动讲出来：

```
证明（鸽巢原理 / 反证法）：

最终我们只要全局最新的 20 条。
考虑最坏情况：这 20 条全部来自同一个作者 A（比如 A 是个刷屏机器人，
刚刚连发 20 条，且都比其他 199 人的所有帖子都新）。
→ 此时作者 A 最多贡献 20 条，其余 199 人贡献 0 条。

因此：任何单个作者对 Top 20 的贡献上限 = 20 条。
     取多于 20 条的部分，100% 是无用功，必然在归并中被丢弃。

∴ 每路只取 20 条即可保证结果完全正确（不是近似，是精确解）。
```

**收益量化**：

| 每路取多少条 | 总候选量 | 网络传输量（每条 8B tweet_id） | 结果正确性 |
|--------------|----------|--------------------------------|-----------|
| 全部（假设人均 500 条） | 100,000 条 | 800 KB | ✅ 正确但浪费 25 倍 |
| **20 条** ⭐ | **4,000 条** | **32 KB** | ✅ **精确正确** |
| 5 条 | 1,000 条 | 8 KB | ❌ **可能错**（有人连发 20 条时漏数据） |

推广：**取第 p 页（每页 20 条）时，每路需要取 `(p × 20)` 条**，或者用游标法只取 `cursor` 之后的 20 条（见下）。

#### 分页游标（Cursor）怎么处理

❌ **绝对不要用 `OFFSET / LIMIT`**：Pull 模型下翻到第 10 页，每路要取 200 条，候选量爆炸到 4 万条；而且期间有新帖插入会导致**重复或漏条**。

✅ **方案 A：游标重新归并（推荐，无状态）**

```python
# 游标 = 上一页最后一条的 tweet_id（Snowflake 自带全局时序，天然可比较）
# 每一页都重新扇出归并，但每路的查询条件变成 "tweet_id < cursor LIMIT 20"
"SELECT tweet_id FROM user_timeline WHERE author_id=? AND tweet_id < ? ORDER BY tweet_id DESC LIMIT 20"
```

| 特性 | 表现 |
|------|------|
| 每页成本 | **恒定** 200 路 × 20 条，不随页码增长 ✅ |
| 服务无状态 | ✅ 任意机器都能处理下一页，天然支持负载均衡 |
| 新帖插入 | ✅ 不会重复/漏条（游标锚定在时间线上） |
| 代价 | ❌ 每页都要重新付一次"200 路扇出"的钱 |

✅ **方案 B：缓存归并中间态（Session Cache，省钱但有状态）**

```
第 1 页归并时，一次性每路取 100 条（而不是 20 条），归并出 Top 100，
把这 100 条 tweet_id 缓存起来：

  SET feed:session:{user_id}:{session_id} <100个tweet_id> EX 300

  第 1 页 → 取缓存 [0:20]    命中，0 次扇出 ✅
  第 2 页 → 取缓存 [20:40]   命中，0 次扇出 ✅
  ...
  第 6 页 → 缓存耗尽，重新扇出归并下一批 100 条
```

| 对比 | 方案 A（每页重算） | 方案 B（缓存中间态） |
|------|--------------------|----------------------|
| 扇出次数 / 5 页 | 5 次 × 200 路 = **1000 次查询** | 1 次 × 200 路 = **200 次查询** ⭐ 省 80% |
| 服务状态 | 无状态 ✅ | 有状态（依赖外部 Redis）⚠️ |
| 内存成本 | 0 | 100 × 8B ≈ 800B/会话；1000 万并发会话 ≈ **8 GB** |
| 实时性 | 每页都最新 ✅ | 翻页期间看不到新帖（TTL 5min 内）⚠️ |
| 适用 | 首屏、低频翻页 | 用户连续快速下拉 |

> ⭐ **工程实践**：两者结合 —— 首屏用方案 A 保证实时，用户开始下拉后切到方案 B 保证流畅。

---

### 优点

| # | 优点 | 具体量化 / 说明 |
|---|------|-----------------|
| 1 | ✅ **写入极快，无写放大** | 发一条推文 = **1 次 DB 写入**。写 QPS 平均 6,000 / 峰值 5 万，单个分片集群轻松扛住。Push 模型峰值写放大到 **1,000 万次/秒**，这里是 **0 放大** |
| 2 | ✅ **完全没有 Celebrity Problem** | 1 亿粉丝的头部账号发帖，成本和 0 粉丝新号**一模一样**（1 次写）。Push 模型下这一条要写 1 亿次、耗时数分钟；Pull 下是 **10ms** |
| 3 | ✅ **存储极省（无冗余副本）** | Push：5 亿条/天 × 200 份 = **1000 亿行/天**，按每行 ~30B 算 ≈ **3 TB/天、1.1 PB/年**（还没算大 V 的畸形放大）。Pull：**5 亿行/天 ≈ 15 GB/天、5.5 TB/年**，省了 **200 倍** |
| 4 | ✅ **数据强实时，无一致性延迟** | 读的瞬间才去拉，读到的**一定是此刻最新的**。Push 模型大 V 发帖后，尾部粉丝可能要等几十秒到几分钟才能看到 |
| 5 | ✅ **关注/取关立即生效，无需 backfill** | 关注一个人 → 下次刷新立刻看到他的历史帖。Push 模型必须做 backfill（把对方最近 N 条灌进你的 Timeline）和 cleanup（取关时从你的 Timeline 里删掉他的帖子），是一堆脏活 |
| 6 | ✅ **不为僵尸用户浪费一分钱** | 2 亿 DAU 背后可能有 10 亿注册用户，其中 80% 是僵尸号。Push 会为这些永远不登录的账号写入并存储 Timeline；Pull **只为真正来读的人付费**，天然按需计算 |
| 7 | ✅ **删除/隐私变更简单** | 推文删除、账号转私密 → 只需改 1 处源数据。Push 模型要去 200 个（大 V 是 1 亿个）粉丝的 Timeline 里删，几乎不可能做干净 |
| 8 | ✅ **系统简单，无异步链路** | 不需要 Kafka + 大规模 Fanout Worker 集群，没有消息积压、重复消费、乱序等一堆分布式难题 |

---

### 致命缺陷

#### 1. ⚠️ 读延迟高且**不可控**：尾延迟放大（Tail Latency Amplification）

这是 Pull 模型的**头号杀手**，也是面试中最能体现深度的点。

一次 Feed 请求 = **扇出 200 路并发查询**，而请求的总耗时不是"平均耗时"，而是 **max(200 路)** —— 木桶效应，被最慢的那一路决定。

```
理想（错误的）直觉：
   单路 5ms，并发 200 路 → 总耗时还是 5ms  ❌ 大错特错

现实：
   ┌──────────────────────────────────────────────────┐
   │ 路 1   ██ 4ms                                    │
   │ 路 2   ██ 5ms                                    │
   │ 路 3   ███ 6ms                                   │
   │ ...                                              │
   │ 路 187 ██ 5ms                                    │
   │ 路 188 ████████████████████████████████ 120ms ⚠️ │ ← GC / 磁盘抖动 / 网络重传
   │ ...                                              │
   │ 路 200 ██ 4ms                                    │
   └──────────────────────────────────────────────────┘
   总耗时 = max = 120ms   ← 199 路都很快，但被 1 路拖垮
```

**概率量化**（假设各路独立，单次查询有 p 的概率变慢）：

> 至少一路命中慢查询的概率 = **1 − (1 − p)^K**

| 单次查询慢的概率 p | 扇出 K=50（活跃过滤后） | **扇出 K=200（默认关注数）** | 扇出 K=5000（重度用户） |
|--------------------|------------------------|------------------------------|-------------------------|
| p = 1%（即单次 P99 = 50ms） | 39.5% | 🔴 **86.6%** | ≈ **100%** |
| p = 0.1%（单次 P99.9 = 200ms） | 4.9% | 🟡 **18.1%** | 99.3% |
| p = 0.01% | 0.5% | 2.0% | 39.3% |

> 🔴 **核心结论（面试原话可以这么说）**：
> "如果单次查询的 P99 是 50ms，扇出 200 路之后，**有 86.6% 的 Feed 请求会至少命中一次慢查询**。也就是说，**单路的 P99 变成了整体的 P50**。我要的整体 P99 < 200ms，反推回去要求单路的 **P99.99** 都必须 < 200ms —— 这在真实生产环境里几乎不可能稳定做到。所以裸的 Pull 模型**根本达不到 P99 < 200ms 的 SLA**，必须上超时降级。"

**延迟拆解表**（乐观估计，Redis 全命中）：

| 阶段 | 耗时 |
|------|------|
| 查关注列表（Redis） | 1 ms |
| **扇出 200 路取 Timeline** | **max(200) ≈ 30~150 ms** 🔴 不可控 |
| N 路归并（内存） | 0.3 ms |
| Hydrate 20 条正文 | 3 ms |
| 网络 + 序列化 | 5 ms |
| **合计 P50** | ~40 ms ✅ |
| **合计 P99** | **> 300 ms** 🔴 **超出 200ms 目标** |

#### 2. 🔴 读 QPS 放大：**7000 万次 DB 查询/秒**

这是最触目惊心的数字，面试中必须算出来：

```
╔══════════════════════════════════════════════════════════════╗
║   平均：35 万 读QPS × 200 关注 = 7,000 万 次查询/秒  🔴         ║
║   峰值：100 万 读QPS × 200 关注 = 2 亿    次查询/秒  🔴🔴       ║
╚══════════════════════════════════════════════════════════════╝
```

这个数字意味着什么：

| 存储介质 | 单实例能力 | **需要多少台（平均态）** | **需要多少台（峰值）** |
|----------|-----------|--------------------------|------------------------|
| MySQL（主键点查） | ~1 万 QPS | **7,000 台** 🔴 | 20,000 台 🔴🔴 |
| Cassandra / HBase | ~3 万 QPS | 2,300 台 | 6,700 台 |
| Redis（单线程） | ~10 万 QPS | **700 台** | **2,000 台** |
| Redis Cluster（多核） | ~50 万 QPS | 140 台 | 400 台 |

> 🔴 **对数据库是灾难**：直接打 MySQL 需要 7,000 台机器**只为了读**，成本上完全不可接受，而且这还没算副本和容灾。
> ⭐ **这直接推导出 Pull 模型的生存前提：User Timeline 必须全部放进 Redis，绝不能落到磁盘型数据库上。**

#### 3. 重复计算：**同一份结果被反复算 N 遍**

```
用户 A 在地铁上 5 分钟内下拉刷新了 10 次：
  Push 模型：10 次 ZREVRANGE       = 10 次 Redis 操作
  Pull 模型：10 × 200              = 2,000 次查询 + 10 次归并  🔴 完全零复用
```

全站量化：日均 300 亿次读 / 2 亿 DAU = **每人每天刷 150 次**。绝大部分相邻两次刷新之间**根本没有新内容**，但每次都要把 200 路扇出 + 归并从头做一遍。这 150 次里可能有 100 次算出的是**完全相同的 20 条**。

#### 4. 关注数越多越慢：**用户体验不公平**

Pull 模型下，用户的 Feed 延迟与关注数 K **线性相关**（网络扇出）+ **对数相关**（归并）：

| 用户类型 | 关注数 K | 扇出查询数 | 尾延迟命中率(p=1%) | 体验 |
|----------|---------|-----------|---------------------|------|
| 冷启动新用户 | 5 | 5 | 4.9% | ✅ 极快，10ms |
| 普通用户 | 200 | 200 | 86.6% | 🟡 勉强，P99 300ms |
| 重度用户 / 记者 | 2,000 | 2,000 | ≈100% | ❌ P99 > 1s |
| 极端用户 / 爬虫号 | 5,000+ | 5,000+ | 100% | ❌ **直接超时** |

> ⚠️ 讽刺的是：**关注最多的用户往往是最活跃、最有价值的用户**，Pull 模型偏偏对他们最差。这跟 Push 模型的 Celebrity Problem 正好构成**对偶关系**：
> - Push 怕**粉丝多**的人（写扇出爆炸）
> - Pull 怕**关注多**的人（读扇出爆炸）

#### 5. 缓存难做：Home Timeline 是**每人独有的组合**

```
Home Timeline = f(你的 200 个关注的组合)

2 亿 DAU × 每人一个独特的关注组合 = 2 亿种不同的结果
→ 结果缓存的 key 空间 = 2 亿，且每来一条新帖就要失效一大批
→ 命中率低、失效风暴、内存放不下 —— 缓存基本失效 ❌

对比 Push：Home Timeline 是预先物化好的实体，本身就是"缓存"，命中率 ~100% ✅
```

**但注意**：虽然 Home Timeline 不好缓存，**User Timeline 却极其好缓存** —— 这正是下面优化手段的突破口。

---

### 优化手段（Pull 不是不能用，关键是怎么救）

> ⭐ **这一节是区分 Senior 和 Staff 的地方**。只会说"Pull 读慢所以不好"是 Senior；能说清楚"Pull 靠哪几招把 7000 万 QPS 打下来、把 P99 拉回 200ms"才是 Staff。

#### ⭐ 优化 1：User Timeline 缓存 —— **Pull 模型能存活的最大前提**

**关键洞察**：Home Timeline 是**每人独有**的（不可共享），但 User Timeline 是**作者维度**的，**可以被所有粉丝共享**！

```
        ┌─────────────────────────────────────────────┐
        │  Redis: ut:{celebrity_id}  (ZSET)           │
        │  score = tweet_id (Snowflake，自带时序)      │
        │  最近 100 条推文                              │
        └──────────────┬──────────────────────────────┘
                       │  被 1 亿粉丝共用同一份缓存 ⭐⭐⭐
     ┌────────┬────────┼────────┬────────┬────────┐
     ▼        ▼        ▼        ▼        ▼        ▼
  粉丝1    粉丝2    粉丝3    ...   粉丝99,999,999  粉丝1亿

  → 这个 key 的缓存命中率无限接近 100%，因为它一直是热的
  → 大 V 的 Timeline 一秒被读几十万次，永远不会被 LRU 淘汰
```

```bash
# 写入：发帖时同步写一次 ZSET（保留最近 100 条）
# ⚠️ score 必须用毫秒时间戳（41 位，< 2^53 安全），绝不能放完整 Snowflake ID（见 §3 / §8.1.5）
#    member 用定长零填充的 tweet_id —— 同 score 时 Redis 按 member 字典序排，零填充保证字典序 == 数值序
ZADD  ut:8823  1755230401123  "1897654321098765432"    # score = 毫秒时间戳，member = tweet_id
ZREMRANGEBYRANK ut:8823  0  -101                       # 只保留最新 100 条，控制内存
EXPIRE ut:8823 86400                                   # 冷作者 1 天后自动淘汰

# 读取：取 cursor 之前的最新 20 条（Pull 扇出的单路操作）
ZREVRANGEBYSCORE  ut:8823  (1735689600123456  -inf  LIMIT 0 20

# 扇出时用 Pipeline 批量打包，把 200 次 RTT 压缩成 ~10 次（按 Redis slot 分组）
```

**容量估算**：

| 项 | 计算 | 结果 |
|----|------|------|
| 需缓存的活跃作者数 | 每天发帖用户约 5,000 万 | 5×10⁷ 个 key |
| 每个 ZSET 存 100 条 | 100 × (8B member + 8B score + skiplist 开销 ≈ 64B) | ~6.4 KB / key |
| **总内存（100 条）** | 5,000 万 × 6.4 KB | **~320 GB** |
| **总内存（只存 20 条）** | 5,000 万 × 1.3 KB | **~65 GB** ⭐ |
| Redis 集群规模 | 按 64GB/节点 + 1 副本 | **10~20 个节点** ✅ 完全可接受 |

**效果对比**：

| 指标 | 无 User Timeline 缓存 | **有 User Timeline 缓存** |
|------|----------------------|---------------------------|
| 后端介质 | MySQL / Cassandra | **Redis** |
| 单路延迟 P50 | 5~10 ms | **0.3 ms** |
| 单路延迟 P99 | 50~200 ms | **2 ms** ⭐ |
| 承载 7000 万 QPS 需要 | 7,000 台 MySQL 🔴 | **140 个 Redis Cluster 节点** ✅ |
| 尾延迟命中率(K=200) | 86.6% 🔴 | **< 5%** ✅ |

> 💡 **面试原话**："Pull 模型可行的**唯一前提**是 User Timeline 全部驻留在内存里。一旦某一路要回源磁盘，扇出 200 路几乎必然有一路慢，P99 就崩了。而 User Timeline 缓存的美妙之处在于它是**共享缓存**——一个 1 亿粉的大 V，1 亿人读的是同一个 Redis key，命中率接近 100%，这是 Home Timeline 缓存永远做不到的。"

#### ⭐ 优化 2：并发拉取 + **超时降级**（把不可控的 P99 变成可控的上界）

```python
FANOUT_TIMEOUT_MS = 50   # 硬超时

results, degraded_count = [], 0
try:
    # ⭐ 超时是 as_completed 生成器在 __next__ 里抛的，不是 fut.result() 抛的，
    #    所以 try 必须包住【整个 for 语句】；写在循环体内永远接不到，异常会冒泡成 500
    for fut in as_completed(futures, timeout=FANOUT_TIMEOUT_MS / 1000):
        try:
            results.append(fut.result())
        except Exception:
            degraded_count += 1              # 单路失败（Redis 报错等），只丢这一路
except TimeoutError:                         # 50ms 到点，无论如何都往下走
    unfinished = [f for f in futures if not f.done()]
    degraded_count += len(unfinished)        # 剩下没返回的路全部丢弃，不阻塞整体
    for f in unfinished:
        f.cancel()                           # 只能取消尚未开始执行的，已在跑的让它自然结束

# results = 「50ms 内拿到的部分结果」，直接进归并堆 —— 宁可少几条，也不能卡住
# 注：Python 3.11+ concurrent.futures.TimeoutError 已是内置 TimeoutError 的别名；
#     3.8~3.10 需 from concurrent.futures import TimeoutError
```

| 策略 | 效果 |
|------|------|
| ❌ 无超时（等所有路返回） | P99 = max(200 路) = **不可控**，可能 > 1s |
| ✅ **50ms 硬超时** | **P99 ≤ 50 + 0.3 + 3 + 5 ≈ 60ms** ⭐ 变成**确定性上界** |
| 数据完整性代价 | 丢 1~2 路，Feed 里少 0~2 条推文 —— **用户完全感知不到** |
| 配套手段 | Hedged Request（20ms 未返回就向副本再发一次，取先到的）可把降级率再降一个数量级 |

> 💡 **核心哲学**：**"宁可少几条，也不能卡住。"** 用户看不到 200 个关注里第 187 个人的第 8 条推文，完全没有感知；但页面转圈 1 秒，用户就走了。**Feed 是可以有损的业务**，这是它跟支付/交易系统最大的区别，务必在面试中点出来。

#### ⭐ 优化 3：只查活跃作者（Active-Author Filtering）

关注列表里的 200 人，**并不是每个人都在发帖**。真实数据分布：

| 关注对象类型 | 占比 | 近 7 天是否发帖 | 是否需要查 |
|--------------|------|-----------------|-----------|
| 高频作者（大 V、媒体） | 10% ≈ 20 人 | ✅ 每天发 | ✅ 必须查 |
| 低频作者 | 15% ≈ 30 人 | ✅ 偶尔发 | ✅ 查 |
| 沉默用户 / 弃号 | 75% ≈ 150 人 | ❌ 半年没发 | ❌ **跳过** |

**实现**：维护一个全局 `active_authors` 的 Bloom Filter 或 Redis SET（近 30 天发过帖的用户），扇出前先做交集过滤。

```bash
# 方案：Redis SET 交集，一次 RPC 拿到需要查的作者
SINTERSTORE  tmp:{uid}  following:{uid}  active_authors_30d
SMEMBERS     tmp:{uid}       # 从 200 人降到 ~50 人
```

**收益量化**：

| 指标 | 优化前 | **优化后** | 降幅 |
|------|--------|-----------|------|
| 扇出路数 K | 200 | **50** | **-75%** |
| 平均 DB/Redis 查询 QPS | 7,000 万 | **1,750 万** | **-75%** 🎉 |
| 峰值查询 QPS | 2 亿 | 5,000 万 | -75% |
| 尾延迟命中率(p=1%) | 86.6% | **39.5%** | 显著改善 |
| 归并堆大小 log K | 7.6 | 5.6 | 略降（本就不是瓶颈） |

#### ⭐ 优化 4：结果缓存 + 短 TTL（应对连续刷新）

针对"缺陷 3：重复计算"，给最终归并结果加一层短 TTL 缓存：

```bash
# 归并完成后写入结果缓存
SET  feed:result:{user_id}  <20个tweet_id的序列化>  EX 30    # 30 秒 TTL

# 下次请求先查这一层
GET  feed:result:{user_id}   # 命中则直接 hydrate 返回，0 次扇出 ⭐
```

| 参数 | 分析 |
|------|------|
| TTL 选 **30 秒** | 平衡点：既能吃掉用户"连刷 3~5 次"的重复请求，又保证内容不会太陈旧 |
| 预期命中率 | **40%~55%**（每人每天刷 150 次，大量是短时间内的连刷） |
| 读 QPS 削减 | 35 万 → **~18 万**，扇出查询从 1,750 万 → **~900 万/秒** ⭐ |
| 内存成本 | 20 × 8B ≈ 160B/人；假设 2,000 万人 30 秒内活跃 → **~3.2 GB** ✅ 极便宜 |
| ⚠️ 代价 | 破坏了 Pull "强实时"的优点，最长 30 秒看不到新帖（但这已经**优于 Push 的秒级~分钟级延迟**） |
| 🟡 优化 | 用户**主动**下拉刷新时带 `force_refresh=1` 绕过缓存；被动进入页面走缓存 |

#### 优化效果总账（叠加后）

```
                    扇出查询 QPS（平均态）
  裸 Pull            ████████████████████████████████  7,000 万  🔴
  + 活跃作者过滤      ████████                          1,750 万  (-75%)
  + 结果缓存(30s)     ████                                900 万  (-87%)
  + 全 Redis 承载     ✅ 900 万 QPS ÷ 50 万/节点 ≈ 20 个节点   完全可行

                    Feed 延迟 P99
  裸 Pull            ████████████████████  > 300 ms  🔴 超标
  + UT 全内存        ████████              ~120 ms
  + 50ms 超时降级    ████                  **~60 ms**  ✅ 远低于 200ms 目标
```

> ⭐ **面试收尾金句**："经过这四步优化，Pull 模型的读放大从 7000 万 QPS 降到 900 万，P99 从不可控的 300ms+ 变成确定性的 60ms。**Pull 不是不能用，而是它必须配一整套内存化 + 剪枝 + 降级的基础设施**。但它有个根本天花板：只要关注数继续涨，扇出就继续涨 —— 所以最终的工业答案是 Push/Pull 混合。"

---

### 适用场景

#### ✅ 适合用 Pull 的场景

| 场景 | 原因 | 典型产品 |
|------|------|----------|
| ✅ **关注数少的产品**（K < 50） | 扇出小，尾延迟命中率低（K=50 时仅 39.5%，K=10 时仅 9.6%）；延迟完全可控 | 微信朋友圈（好友上限 5000 但实际人均几百）、Slack 频道、企业协作工具 |
| ✅ **写多读少 / 读写比接近 1:1** | Pull 把成本压在读上；读少 = 总成本低。本题读写比 60:1 恰恰是 Pull 最不利的情况 | IoT 日志流、内部审计流、监控告警流 |
| ✅ **大 V 极多的场景** | 大 V 发帖成本恒定 = 1 次写，彻底规避 Celebrity Problem。1 万个大 V、头部 1 亿粉丝，Push 下要写 1 亿次，Pull 下 **10ms 搞定** | 新闻/媒体聚合、明星入驻型社区 |
| ✅ **冷启动 / 长尾用户** | 新用户关注 5 人，扇出 5 路 = 10ms 就返回；而 Push 要为他预物化一整条 Timeline，纯浪费 | 新注册用户前 7 天、僵尸/低频用户（占注册量 80%） |
| ✅ **强实时性要求** | 读时才算，读到的**一定是最新**，无一致性延迟 | 股票/赛事直播流、突发新闻流 |
| ✅ **社交图高频变动** | 关注/取关立即生效，不需要 backfill / cleanup | 推荐驱动型产品、频繁调整关注关系的场景 |
| ✅ **存储成本敏感** | 只存 1 份，比 Push 省 200 倍（5.5 TB/年 vs 1.1 PB/年） | 创业初期、成本受限团队 |

#### ❌ 不适合用 Pull 的场景

| 场景 | 原因 | 量化 |
|------|------|------|
| ❌ **本题主场景（类 Twitter）** | 读写比 60:1，把成本压在读上是**方向性错误** | 读扇出 7,000 万 QPS，需 7,000 台 MySQL |
| ❌ **关注数多的用户**（K > 1000） | 扇出线性膨胀，尾延迟命中率 ≈ 100% | K=5000 时 P99 > 1s，**必然超时** |
| ❌ **严格低延迟 SLA**（P99 < 100ms） | 尾延迟放大不可控，只能靠"丢数据"来兜底 | 单路 P99=50ms → 整体 P99 > 300ms |
| ❌ **User Timeline 放不进内存** | 一旦回源磁盘，200 路里必然有慢路 | 落盘后单路 P99 从 2ms → 200ms，直接崩 |
| ❌ **Feed 需要复杂排序/算法推荐** | 排序需要全量候选集打分，读时现算成本爆炸 | 4,000 条候选逐条跑排序模型 = 不可能在 200ms 内完成 |
| ❌ **超高读 QPS**（> 10 万） | 任何放大系数在这个量级都是灾难 | 35 万 × 200 = 7,000 万，峰值 2 亿 |

#### 🟡 最终判定（本题场景）

> 🟡 **结论**：在 **2 亿 DAU、人均关注 200、读写比 60:1、P99 < 200ms** 的设定下，
> **纯 Pull 模型不可行** —— 7,000 万 QPS 的读放大和 86.6% 的尾延迟命中率是两个无法绕过的硬伤。
>
> **但 Pull 的思想是必需的**：它是解决 Celebrity Problem 的**唯一正确工具**。
> 头部 1 万个大 V（粉丝 > 100 万）的推文用 **Pull**（读时拉，成本恒定），
> 剩下 99.99% 的普通用户用 **Push**（写时推，读取 O(1)），
> → 这就自然推导出下一节的 **Hybrid Model（混合模型）**。

```
       Push 的痛点              Pull 的痛点
    ┌────────────────┐      ┌────────────────┐
    │ Celebrity      │      │ 关注数多的用户   │
    │ 粉丝 1 亿 → 写  │      │ 关注 5000 → 读  │
    │ 放大 1 亿倍 🔴  │      │ 放大 5000 倍 🔴 │
    └───────┬────────┘      └────────┬───────┘
            │                        │
            │  ⭐ 两者的痛点正好互补   │
            └───────────┬────────────┘
                        ▼
              ┌───────────────────┐
              │  Hybrid Model     │
              │  普通人 → Push     │
              │  大 V   → Pull     │
              └───────────────────┘
```

---

## 七、策略三：Hybrid Model（混合方案）⭐ 生产环境的标准答案

### 核心思想

> 💡 **一句话**：对普通用户用 **Push（Fan-out on Write，写时扇出）**，对大 V 用 **Pull（Fan-out on Read，读时拉取）**，读 Feed 时把两部分**归并**起来返回给用户。

本质是一句更抽象的话：

> ⭐ **按"扇出成本"对用户分类，让每一类走最划算的那条路。**

Push 和 Pull 不是两个互斥的架构选型，而是**同一个成本函数的两端**：

| | Push（写时扇出） | Pull（读时拉取） |
|---|---|---|
| 成本发生在 | **写侧**，与**粉丝数**成正比 | **读侧**，与**被读次数**成正比 |
| 最怕什么 | 粉丝数爆炸（1 亿粉丝 → 1 亿次写） | 关注数爆炸（关注 200 人 → 200 次查询） |
| 最适合谁 | 粉丝少的**普通用户**（长尾） | 粉丝多的**大 V**（头部） |

我们的场景里，这两端的分布极度不均衡（幂律分布）：

```
关注边总数 = 2 亿 DAU × 200 关注 = 400 亿条边

           粉丝数
             ▲
        1亿  │ ●  ← 头部账号（几十个），一条推文 = 1 亿次写
             │ ●●
       100万 │───●●●●─────────────── celebrity 阈值线
             │      ●●●●●
        1万  │           ●●●●●●●●
             │                    ●●●●●●●●●●●●●
         100 │                                 ●●●●●●●●●●●●●●●●●●●●
             └──────────────────────────────────────────────────────►
              1万个大V                              2亿个普通用户
              占据约 50% 的关注边                    占据约 50% 的关注边
```

**只要把头部这 1 万个账号（占全站账号数的 0.005%）挑出来单独处理，就能砍掉绝大部分扇出成本。**

一组关键数字（后面会反复引用）：

| 方案 | 每天扇出写入次数 | 平均写 QPS | 峰值写 QPS |
|---|---:|---:|---:|
| 纯 Push（全量扇出） | ≈ **1,000 亿** | ≈ 115 万 | ≈ 900 万 ❌ 不可行 |
| Hybrid（剔除大 V） | ≈ **500 亿** | ≈ 58 万 | ≈ 480 万 🟡 |
| Hybrid + 活跃过滤 + 截断 | ≈ **250 亿** | ≈ **29 万** | ≈ 240 万 ✅ 可行 |

> 📌 **推导**：全站扇出总量 = 5 亿 × 200 = **1,000 亿/天**（全文核心数字）。拆开看：普通用户发帖 ≈ 5 亿条/天 × 平均 120 粉丝 ≈ **600 亿**；大 V 发帖 2 万条/天（1 万个大 V × 每天 2 条）× 平均 200 万粉丝 = **400 亿**。也就是说 **0.004% 的推文，贡献了 40% 的扇出量**（与前文「0.01% 的账号贡献 40% 扇出压力」一致）。Hybrid 干掉的就是这 400 亿。

---

### 完整架构图

```
                              ┌──────────────┐
                              │    Client    │
                              │  (App / Web) │
                              └───┬──────▲───┘
                     POST /tweet  │      │  GET /feed?cursor=xxx
                                  ▼      │
                            ┌────────────┴─────────────┐
                            │       API Gateway        │
                            │  认证 / 限流 / 路由       │
                            └───┬──────────────────▲───┘
                                │                  │
       ═══ 写路径 (Write Path) ═╪══════════════════╪═ 读路径 (Read Path) ═══
                                │                  │
                                ▼                  │
                     ┌────────────────────┐        │
                     │   Tweet Service    │        │
                     │ 1. 生成 tweet_id   │        │
                     │    (Snowflake)     │        │
                     │ 2. 落库 + 写缓存    │        │
                     │ 3. 发 Kafka 事件    │        │
                     └───┬────────────┬───┘        │
                         │            │            │
              ┌──────────┘            └──────────┐ │
              ▼                                  ▼ │
    ┌──────────────────┐              ┌──────────────────────┐
    │   Cassandra      │◄─────────────┤     Tweet Cache      │
    │  (Tweet 存储)     │  cache miss  │  (Redis, 正文/KV)     │
    │  PK: tweet_id    │   回源        │  tweet:{id} → JSON   │
    │  + user_timeline │              │  TTL 7d, 命中率 98%   │
    │    (author_id,   │              └───────────▲──────────┘
    │     tweet_id)    │                          │ ⑤ hydrate 正文
    └──────────────────┘                          │
              │                                   │
              ▼ ① 事件投递                         │
    ┌────────────────────────────────────────┐    │
    │              Kafka                     │    │
    │  topic: tweet_created (按 author_id 分区)│   │
    │  ├─ fanout_hi  (在线粉丝, SLA 5s)       │    │
    │  └─ fanout_lo  (离线粉丝, SLA 5min)     │    │
    │  作用：削峰 / 背压 / 重试 / 解耦          │    │
    └───────────────┬────────────────────────┘    │
                    │ ② 消费                       │
                    ▼                             │
    ┌────────────────────────────────────────┐    │
    │           Fanout Service               │    │
    │  ┌──────────────────────────────────┐  │    │
    │  │ if is_celebrity(author):         │  │    │
    │  │     ❌ 不扇出 → 走 Pull            │  │    │
    │  │     只写自己的 User Timeline      │  │    │
    │  │ else:                            │  │    │
    │  │     ✅ 扇出 → 走 Push             │  │    │
    │  └──────────────────────────────────┘  │    │
    └──────┬──────────────────────┬──────────┘    │
           │ ③ 查粉丝列表           │ ④ 批量写      │
           ▼                      ▼               │
  ┌──────────────────┐   ┌──────────────────────┐ │
  │ Follower Graph   │   │   Redis Feed Cache   │ │
  │    Service       │   │  ┌────────────────┐  │ │
  │  followers:{uid} │   │  │ feed:{user_id} │  │ │
  │  following:{uid} │   │  │  ZSET, 800 条   │◄─┼─┼──── ⓐ Push 部分
  │  + is_active 位图 │   │  │ member=tweet_id│  │ │      ZREVRANGE 0 99
  │  (MySQL/HBase +  │   │  │ score =时间戳   │  │ │
  │   Redis 缓存)     │   │  └────────────────┘  │ │
  └──────────────────┘   │  ┌────────────────┐  │ │
                         │  │ utl:{celeb_id} │  │ │
   ┌─────────────────┐   │  │ 大V User       │◄─┼─┼──── ⓑ Pull 部分
   │  大V 列表(内存)   │   │  │ Timeline ZSET  │  │ │      全局共享!
   │  celeb_ids: Set │   │  │ 全站仅 1 万个   │  │ │      1 亿粉丝共用一份
   │  ~1万个 id, 80KB│   │  │ 总计 < 100MB   │  │ │
   │  每台机器一份     │   │  └────────────────┘  │ │
   └─────────────────┘   └──────────────────────┘ │
                                                  │
                         ┌────────────────────────┴──────────────┐
                         │       Feed Service（归并层）           │
                         │  1. ZREVRANGE feed:{me}  ← Push 路    │
                         │  2. 取我关注的大V (∩ celeb_ids)        │
                         │  3. 并发 MGET utl:{c} ← Pull 路 ⚡     │
                         │  4. 归并 + 按 tweet_id 去重             │
                         │  5. Ranking Service 打分排序            │
                         │  6. 截断 20 条 + hydrate 正文           │
                         └───────────────────────────────────────┘
```

**两条路径一句话总结：**

| 路径 | 谁触发 | 走 Push 还是 Pull | 代价 |
|---|---|---|---|
| **写路径**（普通用户发帖） | 4.998 亿条/天 | 🟢 **Push**：扇出到 ~100 个活跃粉丝的 ZSET | 500 亿次 Redis 写/天 |
| **写路径**（大 V 发帖） | 20 万条/天 | 🔵 **Pull**：只写自己的 `utl:{celeb_id}`，**1 次写** | 20 万次写/天（可忽略） |
| **读路径** | 300 亿次/天 | 🟢 Push 部分：**1 次** `ZREVRANGE` | 35 万 QPS |
| **读路径** | 300 亿次/天 | 🔵 Pull 部分：并发读 ~20 个共享缓存 key | 命中率 99.9%+，几乎零成本 |

---

### 判定规则：谁走 Push，谁走 Pull？

这是整个 Hybrid 方案的**心脏**。面试里如果只说"大 V 走 Pull"就结束，是 Mid 级；能把阈值怎么定、动态成本函数、状态切换的坑讲清楚，才是 Senior/Staff 级。

#### 规则 1：静态阈值法（Baseline）

```python
IS_CELEBRITY_THRESHOLD = 1_000_000   # 粉丝数 > 100 万 → 标记为 celebrity

def should_fanout(author_id: int) -> bool:
    """决定这条推文是否需要写时扇出"""
    return get_follower_count(author_id) <= IS_CELEBRITY_THRESHOLD
```

在我们的场景下，阈值 100 万 → 约 **1 万个账号**被标记为 celebrity（全站账号的 0.005%）。

#### 阈值取值的权衡表

| 阈值取值 | 被标为 celebrity 的账号数 | 写侧影响 | 读侧影响 | 判定 |
|---|---:|---|---|---|
| **1 万** | ~200 万个 | ✅ 扇出量再降 40%，峰值写 QPS < 100 万 | ❌ 每个用户平均关注 **60+** 个"大V" → 每次读 Feed 要并发 60 路查询，缓存 key 数量 200 万个（命中率下降），P99 从 80ms 涨到 250ms | ❌ 读侧压力大 |
| **10 万** | ~20 万个 | ✅ 扇出量降 ~15% | 🟡 每用户平均关注 ~35 个大V，尚可接受 | 🟡 激进但可行 |
| **100 万** ⭐ | ~1 万个 | ✅ 砍掉 89% 扇出量（4,000 亿 → 0） | ✅ 每用户平均关注 ~20 个大V（P99 ≈ 50），共享缓存总量 < 100MB | ✅ **推荐** |
| **1,000 万** | ~500 个 | ❌ 粉丝 500 万的账号仍走 Push：一条推文 = 500 万次写，**扇出风暴回归**，单条推文扇出耗时 > 30s | ✅ 每用户平均只关注 2~3 个大V，读侧几乎无成本 | ❌ 写侧尾延迟失控 |
| **不设阈值（纯 Push）** | 0 | ❌ 峰值写 QPS 4,000 万，头部账号发帖需 1 亿次写 → 数小时才扇完 | ✅ 读侧最快 | ❌ 不可行 |

> 💡 **面试话术**：
> "阈值本质上是在**写放大**和**读扇出**之间找平衡点。阈值往下调，写侧越省，但读侧要并发拉的大 V 数量线性增长，同时共享缓存的命中率会下降——因为长尾大 V 的 timeline 被读的次数少，容易被 LRU 淘汰。阈值往上调，写侧就会重新出现扇出风暴。
> 我会取 100 万，因为在这个点上，被标记的账号只有 1 万个，它们的 timeline 缓存总量不到 100MB，**可以整个放进 Feed Service 的本地内存**，Pull 那一路的成本几乎归零。这是一个非常舒服的甜点。"

#### 规则 2：动态阈值 / 成本函数（Staff 级答案）

静态阈值有个致命问题：**它只看粉丝数，不看粉丝的"含金量"。**

反例：
- 账号 A：120 万粉丝，但**每天只发 1 条**，且粉丝里只有 5% 是 DAU → Push 成本 = 1 × 6 万 = **6 万次写/天**。这种账号强行走 Pull，反而让所有关注它的人读路径多一次查询，纯亏。
- 账号 B：80 万粉丝（低于阈值），但**每天发 200 条**（新闻机器人），粉丝 60% 是 DAU → Push 成本 = 200 × 48 万 = **9,600 万次写/天**。这种账号走 Push 会打爆扇出队列，但静态阈值放过了它。

所以真正的判定应该是**成本对比**：

```
┌───────────────────────────────────────────────────────────────┐
│  Push 成本 ≈ 粉丝数(活跃) × 发帖频率 × C_write                 │
│           = N_active_followers × F_post × C_write             │
│                                                               │
│  Pull 成本 ≈ 活跃粉丝数 × 每个粉丝的刷新频率 × C_read           │
│           = N_active_followers × F_refresh × C_read           │
│                                                               │
│  ⭐ 当 Push成本 > Pull成本 时，切换到 Pull                      │
│                                                               │
│  两边同时约掉 N_active_followers（这是关键洞察！）：            │
│                                                               │
│      F_post × C_write  >  F_refresh × C_read                  │
│                                                               │
│      ⇒  F_post / F_refresh  >  C_read / C_write               │
└───────────────────────────────────────────────────────────────┘
```

> ⚠️ **一个反直觉的结论**：数学上 `N_active_followers` 被约掉了 —— **粉丝数本身不决定该走 Push 还是 Pull，"发帖频率 / 粉丝刷新频率"的比值才决定**。

但为什么工程上还是用粉丝数当主判据？因为：

| 原因 | 说明 |
|---|---|
| ✅ **Pull 有缓存放大效应** | `C_read` 不是常数。粉丝越多，`utl:{celeb_id}` 被读得越频繁，缓存命中率越高，**边际读成本趋近于 0**。所以粉丝数越大，Pull 的实际 `C_read` 越低 → 越该走 Pull |
| ✅ **Push 有尾延迟问题** | 除了总成本，还有**单条推文的扇出耗时**。1 亿粉丝即使总成本可接受，扇完也要几十分钟，用户体验直接崩 |
| ✅ **粉丝数好算、好缓存** | 发帖频率和刷新频率需要实时统计，粉丝数是一个现成的计数器 |

**生产上的折中做法：粉丝数为主 + 成本函数为辅的复合判据**

```python
# 常量（离线压测得出，单位归一化到"一次 Redis 操作"）
C_WRITE = 1.0      # 一次 ZADD 的成本
C_READ  = 0.05     # 一次共享缓存读的摊薄成本（因命中率 99.9%，成本极低）
HARD_THRESHOLD = 1_000_000     # 硬阈值：超过必走 Pull（尾延迟保护）
SOFT_THRESHOLD =   100_000     # 软阈值：进入成本函数评估区间

def decide_strategy(user_stats) -> str:
    n = user_stats.active_follower_count      # 近 7 天活跃粉丝数
    f_post = user_stats.posts_per_day         # 该作者日均发帖数
    f_refresh = user_stats.avg_follower_refresh_per_day  # 粉丝日均刷 Feed 次数

    # ① 硬阈值：粉丝太多，单条扇出耗时不可接受 → 无条件 Pull
    if n > HARD_THRESHOLD:
        return "PULL"

    # ② 粉丝很少，扇出白菜价 → 无条件 Push
    if n < SOFT_THRESHOLD:
        return "PUSH"

    # ③ 灰色区间（10万 ~ 100万粉丝）：比成本
    push_cost = n * f_post * C_WRITE          # 写侧：每发一帖，写 n 次
    pull_cost = n * f_refresh * C_READ        # 读侧：每个粉丝每次刷都多查一次
    return "PULL" if push_cost > pull_cost else "PUSH"
```

代入一组真实数字（粉丝 50 万的高频作者）：

| 指标 | 数值 |
|---|---|
| 活跃粉丝数 `n` | 500,000 |
| 日均发帖 `F_post` | 50 条 |
| 粉丝日均刷 Feed `F_refresh` | 15 次 |
| Push 成本 | 500,000 × 50 × 1.0 = **2,500 万** |
| Pull 成本 | 500,000 × 15 × 0.05 = **37.5 万** |
| 结论 | Push 是 Pull 的 **66 倍** → 🔵 **切 Pull** |

再看一个粉丝 50 万的低频作者（每天发 1 条）：

| Push 成本 | 500,000 × 1 × 1.0 = **50 万** |
|---|---|
| Pull 成本 | 500,000 × 15 × 0.05 = **37.5 万** |
| 结论 | 接近，略偏 Pull；此时优先选 Push（读延迟更优）→ 🟢 **保持 Push** |

> 💬 **面试怎么说**：
> "我会用粉丝数做硬阈值兜底，因为它保护的是**单条推文的扇出尾延迟**，这是 SLA 问题不是成本问题。但在 10 万到 100 万这个灰色地带，我会跑一个成本函数，把**发帖频率**和**粉丝活跃度**也算进去。一个日更 200 条的新闻账号，即使只有 30 万粉丝，也应该被当成 celebrity 处理。"

#### 这个标记（is_celebrity）存在哪？

```
┌─────────────────────────────────────────────────────────────────┐
│  三级存储，读多写少的典型缓存模式                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  L0: Fanout Worker 进程内存（本地缓存）        ← 99.99% 命中      │
│      celeb_ids: HashSet<int64>                                  │
│      1 万个 id × 8 字节 = 80 KB，全量常驻                        │
│      每 60s 从 L1 全量刷新一次（或订阅变更事件失效）              │
│      查询耗时: ~50 ns                                            │
│                          ↓ miss / 定时刷新                       │
│  L1: Redis                                                      │
│      SET celebrity:ids  → 全量大V id 集合（SISMEMBER O(1)）      │
│      HASH user:{id}     → {follower_count, is_celebrity,        │
│                            celebrity_since_ts}                  │
│      查询耗时: ~1 ms                                             │
│                          ↓ miss                                  │
│  L2: 用户表（MySQL / 分库分表）                                   │
│      users(user_id PK, follower_count, is_celebrity BOOL,       │
│            celebrity_since_ts BIGINT, updated_at)               │
│      INDEX idx_celeb (is_celebrity)  ← 便于全量拉取              │
│      真相源（Source of Truth）                                    │
└─────────────────────────────────────────────────────────────────┘
```

**为什么可以放心地全量放进本地内存？**

```
1 万个大 V × 8 字节 (int64 id) = 80 KB
即使冗余到 10 万个（阈值降到 10 万粉丝）也只有 800 KB
→ 每台 Fanout Worker / Feed Service 常驻一份，零网络开销 ✅
```

**标记是怎么更新的？**

| 步骤 | 做法 |
|---|---|
| ① 计数 | 关注/取关时异步 `INCR/DECR user:{id}:follower_count`（不走强一致，允许几秒延迟） |
| ② 判定 | 后台任务每 **5 分钟**扫描一次"粉丝数接近阈值"的候选集（`900K < count < 1.1M`），重新评估 |
| ③ 落库 | 命中规则 → 更新 MySQL `is_celebrity=1, celebrity_since_ts=now()` |
| ④ 广播 | 发一条 `celebrity_status_changed` 事件到 Kafka，各节点失效本地缓存 |
| ⑤ 兜底 | 本地缓存 TTL 60s，即使事件丢了，最迟 60s 后一致 |

**扇出前查一次的位置：**

```python
def handle_tweet_created(event):
    author_id = event.author_id
    # ⭐ 扇出前的唯一一次判定，命中本地 HashSet，耗时 ~50ns
    if author_id in LOCAL_CELEB_SET:
        write_user_timeline(author_id, event.tweet_id)   # 只写 1 次，不扇出
        return
    fanout_to_followers(author_id, event.tweet_id)       # 正常 Push
```

#### ⚠️ 状态切换的坑：普通用户 → 大 V 的那一刻

这是面试官最爱追问的地方，也是最能区分候选人的问题。

**问题场景：**

```
时间轴 ──────────────────────────────────────────────────────►

 T1: 用户 A 粉丝 99 万（普通用户）
     A 发推文 Tweet-100  →  ✅ Push 扇出到 99 万粉丝的 feed:{follower}
                              同时也写了 A 自己的 utl:{A}

 T2: A 粉丝破 100 万，被标记为 celebrity（celebrity_since_ts = T2）

 T3: A 发推文 Tweet-200  →  🔵 不扇出，只写 utl:{A}

 T4: 粉丝 B 刷 Feed：
     Push 路：ZREVRANGE feed:B      → [Tweet-100, ...]
     Pull 路：ZREVRANGE utl:A       → [Tweet-200, Tweet-100, ...]
                                                    ▲
                                          ❌ Tweet-100 重复了！
```

**同时还有反向的坑（大 V → 普通用户，掉粉降级）：**

```
 T5: A 掉粉到 95 万，被取消 celebrity 标记
 T6: A 发 Tweet-300  →  ✅ 恢复 Push 扇出
 T7: 粉丝 B 刷 Feed：
     Push 路：[Tweet-300, Tweet-100, ...]
     Pull 路：不再拉 utl:A（A 已不是大V）
                    ▲
          ❌ Tweet-200 丢了！（那段时间的推文谁都没扇出，也没人来 Pull）
```

**四种解法对比：**

| 方案 | 做法 | 优点 | 缺点 | 生产采用 |
|---|---|---|---|---|
| **① 读时按 tweet_id 去重** ⭐ | 归并后用 `set`/`dict` 按 `tweet_id` 去重，保留一份 | ✅ 简单粗暴、绝对正确<br>✅ O(n)，n≈150，耗时 < 0.1ms<br>✅ 对所有边界情况都有效（含消息重复投递、扇出重试） | 🟡 多占一点 CPU（可忽略） | ✅ **必做，兜底** |
| **② 以 celebrity_since_ts 为界** | Pull 时只拉 `create_time >= celebrity_since_ts` 的推文（ZSET 用 `ZREVRANGEBYSCORE utl:A +inf {ts}`） | ✅ 从源头消除重叠<br>✅ 减少无效数据传输 | ❌ 依赖时钟一致性<br>❌ 切换瞬间的并发写可能骑在边界上 | ✅ **推荐，与①并用** |
| **③ 降级时回填（backfill）** | 大V → 普通时，把切换期间的推文异步补扇出到粉丝 Feed | ✅ 解决"丢推文"问题 | ❌ 回填本身就是一次大扇出，把省下的成本又吐回去 | 🟡 视情况 |
| **④ 滞后带（Hysteresis）+ 单向降级** ⭐ | 升级阈值 100 万，降级阈值 80 万（差 20%）；且降级需连续 7 天低于阈值 | ✅ **根治抖动**：避免粉丝在 100 万上下反复横跳导致每小时切换十几次<br>✅ 降级极少发生，③ 的成本被摊薄到可忽略 | 🟡 需要多维护一个降级阈值 | ✅ **必做** |

**最终生产做法（组合拳）：**

```python
# ① 升降级用滞后带，杜绝抖动
PROMOTE_THRESHOLD = 1_000_000   # 升级为 celebrity
DEMOTE_THRESHOLD  =   800_000   # 降级回普通用户（差 20% 的滞后带）
DEMOTE_STABLE_DAYS = 7          # 且需连续 7 天低于阈值

# ② Pull 时以切换时间戳为界，避免拉到已 Push 过的历史推文
def pull_celebrity_timeline(celeb_id, since_ts):
    # since_ts = max(celebrity_since_ts, 用户 Feed 的最早时间)
    return redis.zrevrangebyscore(f"utl:{celeb_id}", "+inf", since_ts, start=0, num=100)

# ③ 归并后无条件按 tweet_id 去重（兜底，永远不能省）
seen = set()
merged = [t for t in candidates if not (t.id in seen or seen.add(t.id))]
```

> 💬 **面试怎么说**：
> "我会用**去重兜底 + 滞后带防抖**两层保护。去重是必须的——不光是为了状态切换，Kafka 的 at-least-once 语义、扇出 Worker 的重试，都会产生重复，读时去重是这个系统的**一等公民**，成本只有几十微秒。
> 滞后带解决的是另一个问题：如果升降级都用 100 万这一个阈值，一个卡在临界点的账号会在几小时内被反复切换十几次，每次切换都要广播事件、失效缓存，还可能触发回填。升 100 万、降 80 万，中间留 20% 的死区，抖动就没了。"

---

### 读路径的归并逻辑

#### 完整伪代码

```python
import asyncio
from typing import List, Dict

# ─────────────────────────── 常量配置 ───────────────────────────
PUSH_FETCH_SIZE = 100        # 从预计算 Feed 取 Top 100（首屏只要 20，多取是为了给排序留空间）
PULL_PER_CELEB  = 20         # 每个大V最多拉 20 条（大V发帖密集，20 条足够覆盖近期）
MAX_CELEB_FANIN = 200        # 单用户关注的大V数上限（防止"关注 5000 个大V"的极端账号打爆读路径）
PAGE_SIZE       = 20         # 首屏返回条数


async def get_home_feed(user_id: int, cursor: int = None) -> List[dict]:
    """
    读 Feed 主流程：Push 路 + Pull 路 → 归并 → 去重 → 排序 → 截断 → hydrate
    目标：P99 < 200ms
    """

    # ═══════ ① 从 Redis 读预计算的 Feed（Push 部分）══════════════
    #    这一路已经由 Fanout Service 在写时算好了，读的时候就是一次 O(log N + M) 的 ZSET 范围查询
    #    member = tweet_id (int64)，score = 发帖时间戳（毫秒）或排序分数
    #    耗时：单次 ZREVRANGE 约 0.5~2 ms（100 条 × 8 字节 = 800 字节，网络传输可忽略）
    push_task = asyncio.create_task(
        redis_feed.zrevrange(f"feed:{user_id}", 0, PUSH_FETCH_SIZE - 1, withscores=True)
    )

    # ═══════ ② 查我关注的大V列表 ═══════════════════════════════
    #    关键洞察：全站只有 1 万个大V，id 全量放进进程内存只占 80 KB
    #    所以这一步 **不需要任何网络请求**：拿我的关注列表和本地大V集合求交集
    #    我的关注列表（平均 200 个）也缓存在 Redis，且有本地 LRU，命中率 95%+
    #    结果规模：平均 ~20 个，P99 ~50 个（远小于 200 的关注总数）
    following = await get_following_list(user_id)              # 平均 200 个 id，~1ms（多数命中本地缓存）
    my_celebs = [uid for uid in following if uid in LOCAL_CELEB_SET]   # 纯内存交集，~10 µs
    my_celebs = my_celebs[:MAX_CELEB_FANIN]                    # 极端账号截断保护

    # ═══════ ③ 并发拉取这些大V的 User Timeline（Pull 部分）══════
    #    ⭐ 重点：utl:{celeb_id} 是 **全局共享** 的 —— 1 亿粉丝共用同一份数据
    #    用 Redis Pipeline 把 20 次查询打包成 1 个 RTT，而不是串行 20 次
    #    命中率 99.9%+（这 1 万个 key 是全站最热的 key，永远不会被淘汰）
    #    耗时：1 个 RTT ≈ 1~3 ms（无论关注了 1 个还是 50 个大V）
    pull_task = asyncio.create_task(fetch_celebrity_timelines(my_celebs, user_id))

    # ①③ 并发执行，总耗时 = max(push, pull) 而不是求和
    push_items, pull_items = await asyncio.gather(push_task, pull_task)

    # ═══════ ④ 两路归并 + 按 tweet_id 去重 + 排序 ═══════════════
    #    ⚠️ 去重是必须的：
    #       a) 状态切换期：作者刚变大V，老推文既在 feed 里又在 utl 里
    #       b) Kafka at-least-once：同一条推文可能被扇出两次
    #    数据量：100（Push）+ 20×20（Pull）≈ 500 条，纯内存操作 < 1 ms
    seen: set = set()
    merged: List[tuple] = []
    for tweet_id, score in push_items + pull_items:
        if tweet_id in seen:          # O(1) 哈希查找，天然幂等
            continue
        seen.add(tweet_id)
        merged.append((tweet_id, score))

    # 按分数（时间戳 或 ranking score）降序排列
    # 500 条的排序 ≈ 20 µs，完全不是瓶颈
    merged.sort(key=lambda x: x[1], reverse=True)

    # ═══════ ⑤ 截断到 20 条 + hydrate 正文 ═════════════════════
    #    前面全程只搬运 8 字节的 tweet_id，**不搬正文**，这是省带宽的关键
    #    直到最后一步才把 20 个 id 换成完整对象（一次 MGET，1 个 RTT）
    #    如果先 hydrate 再排序，会白白拉取 480 条无用正文（约 500KB 浪费）
    page = merged[:PAGE_SIZE]
    tweet_ids = [tid for tid, _ in page]
    tweets = await hydrate_tweets(tweet_ids)                   # Tweet Cache MGET，命中率 98%
    return tweets


async def fetch_celebrity_timelines(celeb_ids: List[int], viewer_id: int) -> List[tuple]:
    """并发拉取大V的 User Timeline —— Pull 那一路"""
    if not celeb_ids:
        return []

    # 用 Pipeline 打包，N 次查询 → 1 个网络往返
    pipe = redis_feed.pipeline(transaction=False)
    for cid in celeb_ids:
        # ⭐ 以 celebrity_since_ts 为下界，只拉"成为大V之后"发的推文
        #    避免拉到之前已经 Push 到 viewer feed 里的历史推文（源头减少重复）
        since = CELEB_SINCE_TS.get(cid, 0)                     # 本地内存查表，~50 ns
        pipe.zrevrangebyscore(f"utl:{cid}", "+inf", since,
                              start=0, num=PULL_PER_CELEB, withscores=True)
    results = await pipe.execute()                             # 1 个 RTT，~1~3 ms

    # 扁平化：[[(tid, score), ...], [...]] → [(tid, score), ...]
    return [item for sub in results for item in sub]


async def hydrate_tweets(tweet_ids: List[int]) -> List[dict]:
    """把 tweet_id 换成完整推文对象（正文 / 作者 / 计数）"""
    keys = [f"tweet:{tid}" for tid in tweet_ids]
    cached = await tweet_cache.mget(keys)                      # 1 次 MGET，~2 ms

    # 缓存未命中的回源 Cassandra（只占 2%，且是批量查询）
    misses = [tid for tid, val in zip(tweet_ids, cached) if val is None]
    if misses:
        rows = await cassandra.batch_get_tweets(misses)        # ~10 ms
        await tweet_cache.mset_async(rows, ttl=7 * 86400)      # 异步回填缓存
        cached = merge_results(cached, rows)

    return [json.loads(v) for v in cached if v]
```

#### ⭐ 为什么 Pull 那一路"几乎不花钱"？

这是整个方案最漂亮的地方，面试时**一定要主动讲**：

```
┌───────────────────────────────────────────────────────────────────┐
│  ❌ 如果大V也走 Push：                                             │
│     某头部账号有 1 亿粉丝，发一条推文                               │
│     → 要写 1 亿次 ZADD                                            │
│     → 存储：1 亿份 tweet_id 副本（每份 8 字节 + ZSET 开销 ~70 字节）│
│     → 存储成本 = 1 亿 × 70 B = 7 GB  ← 仅仅为了一条推文！          │
│     → 扇出耗时：│     → 扇出耗时：即使 50 万 QPS 的写入能力，也要 200 秒             │            │
├───────────────────────────────────────────────────────────────────┤
│  ✅ 如果大V走 Pull：                                               │
│     发一条推文 → 只写 1 次 ZADD 到 utl:{celeb_id}                  │
│     → 存储：**1 份**，被 1 亿粉丝共享读取                          │
│     → 存储成本 = 70 字节                                          │
│     → 扇出耗时：< 1 ms                                            │
│                                                                   │
│     🔥 存储放大比：1 亿 : 1                                        │
└───────────────────────────────────────────────────────────────────┘
```

**全站大 V Timeline 缓存的总体积：**

```
1 万个大V × 800 条/timeline × (8B tweet_id + 8B score) = 128 MB (裸数据)
考虑 Redis ZSET 的 skiplist + dict 开销（约 70 B/entry）：
1 万 × 800 × 70 B ≈ 560 MB

→ 全站 Pull 侧的数据总量 < 1 GB，单机 Redis 就能装下
→ 甚至可以在每台 Feed Service 上做本地副本（进程内 LRU），网络请求都省了
```

**对比 Push 侧的存储：**

| 侧 | 数据量 | 备注 |
|---|---:|---|
| **Push 侧** `feed:{user_id}` | 2 亿 DAU × 800 条 × 70 B ≈ **11 TB** | 需要 ~100 台 128GB Redis 分片 |
| **Pull 侧** `utl:{celeb_id}` | 1 万 × 800 条 × 70 B ≈ **0.56 GB** | 单机可装，可全量本地缓存 |

> 💬 **面试点睛**：
> "Pull 那一路的成本之所以可以忽略，是因为**大 V 的 User Timeline 是全局共享的一份数据**。1 亿个粉丝读的是**同一个 Redis key**，这个 key 是全站最热的 key 之一，命中率接近 100%，甚至可以做多层本地缓存。
> 换句话说：**Push 的成本随粉丝数线性增长，Pull 的成本随粉丝数趋于常数**——因为粉丝越多，缓存命中率越高。这就是为什么头部账号必须走 Pull。"

#### 读路径延迟预算（P99 < 200ms 怎么达成）

| 阶段 | P50 | P99 | 说明 |
|---|---:|---:|---|
| API Gateway（认证 + 限流） | 2 ms | 8 ms | JWT 本地校验，不走网络 |
| 取关注列表 + 大V交集 | 0.5 ms | 3 ms | 本地 LRU 命中 95%，miss 走 Redis |
| ① `ZREVRANGE feed:{uid}` (Push) | 0.8 ms | 4 ms | 单 key 单分片，100 条 |
| ③ Pipeline 拉 20 个大V (Pull) | 1.5 ms | 6 ms | 与 ① **并发**，1 个 RTT |
| ④ 归并 + 去重 + 排序（500 条） | 0.3 ms | 1 ms | 纯内存 |
| Ranking Service 打分（可选） | 15 ms | 45 ms | 独立服务，可降级为纯时间序 |
| ⑤ Hydrate 20 条正文 | 3 ms | 12 ms | MGET，2% miss 回源 Cassandra |
| 序列化 + 网络回传 | 5 ms | 25 ms | ~40 KB 响应体，启用 gzip |
| **合计** | **≈ 28 ms** | **≈ 100 ms** | ✅ 留 100ms 余量给 GC / 重试 / 跨 AZ 抖动 |

---

### 其他必须讲的生产优化

> ⭐ 这部分决定面试评级。基础 Hybrid 只能拿到 "Hire"，把下面 5 点讲透才能拿到 "Strong Hire / Staff"。

#### 1️⃣ 只为活跃用户预计算（Active User Filter）

**问题**：2 亿 DAU 背后是 **20 亿注册用户**。给一个 3 年没登录的僵尸账号预计算 Feed，是 100% 的浪费。

**定义活跃**：

| 分层 | 定义 | 占比（相对于粉丝集合） | 扇出策略 |
|---|---|---:|---|
| 🔥 **热活跃** | 近 **7 天**登录过 | ~35% | ✅ 实时扇出（高优队列） |
| 🟡 **温活跃** | 近 **30 天**登录过 | ~20% | ✅ 延迟扇出（低优队列） |
| ❄️ **休眠** | 超过 30 天未登录 | ~45% | ❌ **跳过扇出** |

**实现**：用 Redis Bitmap 存活跃位图，O(1) 判定，内存极省。

```bash
# 用户登录时打标（key 按天分片，便于做 7 天/30 天的 OR 运算）
SETBIT active:20260815 {user_id} 1

# 合并近 7 天的活跃位图（每天凌晨跑一次，生成 active:7d）
BITOP OR active:7d active:20260809 active:20260810 ... active:20260815

# 扇出时判定单个粉丝是否活跃：O(1)
GETBIT active:7d {follower_id}
```

**内存开销**：

```
20 亿用户 × 1 bit = 20 亿 bit = 250 MB     ← 整张活跃位图只要 250MB！
乘以 7 天分片 + 30 天分片 ≈ 10 GB，可接受
```

**收益**：

| | 扇出量/天 | 节省 |
|---|---:|---|
| 不过滤（全量粉丝） | 500 亿 | — |
| 过滤到 30 天活跃（55%） | 275 亿 | **45%** ✅ |
| 过滤到 7 天活跃（35%） | 175 亿 | **65%** ✅ |

**⚠️ 不活跃用户回归时怎么办？（这是必被追问的点）**

```
用户 C 休眠 90 天后重新登录
  ↓
feed:{C} 已被 TTL 淘汰，或残留 90 天前的陈旧数据 → 直接返回会很尴尬
  ↓
┌────────────────────────────────────────────────────────────┐
│  ⭐ 冷启动流程：走【完全 Pull】重建 Feed                     │
│                                                            │
│  1. 登录时检测：feed:{C} 不存在 or last_fanout_ts 过旧      │
│  2. 打标 rebuilding=true，返回一个"加载中"的骨架屏（可选）   │
│  3. 【完全 Pull】拿到 C 的全部 200 个关注                   │
│     → 并发查这 200 个人的 user_timeline（Cassandra/Redis） │
│     → 每人取最近 20 条 → 4000 条候选                        │
│     → 归并 + 排序 + 截断到 800 条                           │
│     → 一次性 ZADD 写回 feed:{C}                            │
│  4. 把 C 加回 active:7d 位图                                │
│  5. 之后所有新推文对 C 恢复正常 Push ✅                     │
│                                                            │
│  耗时：200 路并发查询，P99 ≈ 400~800 ms                     │
│  → 只在"回归后第一次"发生，用户完全能接受                    │
│  → 可以在后台异步重建，首屏先用完全 Pull 实时算一页返回      │
└────────────────────────────────────────────────────────────┘
```

> 💡 **注意这里的美妙之处**：冷启动重建，本质上就是**把纯 Pull 方案当成一个补偿机制来用**。Hybrid 之所以强，是因为它手里握着 Push 和 Pull 两把工具，可以随时切换。

#### 2️⃣ 分级 / 分层扇出（Tiered Fanout）

**问题**：即使过滤掉休眠用户，一个 50 万粉丝的账号发帖仍要写 50 万次。如果所有扇出都要求"秒级完成"，峰值时队列必然堆积。

**洞察**：**不是所有粉丝都需要立刻看到。** 一个正在 App 里下拉刷新的用户需要秒级，一个明天早上才会打开 App 的用户，延迟 5 分钟毫无影响。

```
                      Tweet Created 事件
                             │
                             ▼
                   ┌──────────────────┐
                   │  Fanout Planner  │  查粉丝列表 + 活跃分层
                   └────┬────┬────┬───┘
          ┌─────────────┘    │    └──────────────┐
          ▼                  ▼                    ▼
  ┌───────────────┐  ┌───────────────┐  ┌──────────────────┐
  │ P0: 在线粉丝   │  │ P1: 近期活跃   │  │ P2: 30天+ 休眠    │
  │ (有活跃长连接) │  │ (7~30天登录)   │  │                  │
  │               │  │               │  │                  │
  │ topic:        │  │ topic:        │  │  ❌ 直接丢弃      │
  │  fanout_p0    │  │  fanout_p1    │  │  不扇出           │
  │               │  │               │  │  回归时冷启动重建  │
  │ SLA: < 5 s ⚡ │  │ SLA: < 5 min  │  │                  │
  │ 占比 ~8%      │  │ 占比 ~47%     │  │  占比 ~45%       │
  │ Worker: 200台 │  │ Worker: 50台  │  │                  │
  │ 独立消费组     │  │ 独立消费组     │  │                  │
  │ (不受 P1 阻塞)│  │ 可被限流/降级  │  │                  │
  └───────────────┘  └───────────────┘  └──────────────────┘
```

**关键实现要点：**

| 要点 | 做法 |
|---|---|
| **优先级用独立 topic 实现** | 不要在一个 topic 里做优先级排序（Kafka 不支持）。用 `fanout_p0` / `fanout_p1` 两个 topic + 两个独立消费组，**物理隔离**，P1 堆积不会阻塞 P0 |
| **在线判定** | 用 WebSocket/长连接网关维护 `online:{user_id}` 的 Redis Set，TTL 5 分钟；或用 `active:1h` 位图近似 |
| **P1 可降级** | 系统压力大时，直接**暂停 P1 消费组**，只保 P0。用户体验上完全无感 |
| **批量写** | P1 可以攒批：把 1000 个粉丝的 ZADD 合成一个 Pipeline，减少 RTT。P0 则用小批（50 个）保证低延迟 |

**收益**：

```
需要"秒级"完成的扇出量  = 500 亿 × 8%  = 40 亿/天  → 平均 4.6 万 QPS ✅ 轻松
可以"分钟级"完成的扇出量 = 500 亿 × 47% = 235 亿/天 → 可以削平到低谷时段消费
→ 峰值资源需求下降 **一个数量级**
```

#### 3️⃣ Feed 长度截断（Truncation）

**问题**：如果 `feed:{user_id}` 无限增长，存储会失控。

```
不截断：2 亿 DAU × 平均关注 200 人 × 每人 5 条/天 × 365 天 × 70 B
      ≈ 2 亿 × 36.5 万条 × 70 B = 5,110 TB  ❌ 完全不可行
```

**做法：每个用户只保留最近 800 条。**

```bash
# 扇出写入时，用 Lua 脚本把「写入 + 截断」合成一次原子操作（省一半 RTT）
EVAL "
  redis.call('ZADD', KEYS[1], ARGV[1], ARGV[2])       -- 写入新推文
  redis.call('ZREMRANGEBYRANK', KEYS[1], 0, -801)     -- 只保留分数最高的 800 条
  redis.call('EXPIRE', KEYS[1], 2592000)              -- 30 天无访问自动回收
  return 1
" 1 feed:{user_id} 1755302400123 1899234567890123456
```

> ⚙️ **优化**：`ZREMRANGEBYRANK` 不必每次都调用。可以概率触发（`if random() < 0.05`）或按长度触发（ZSET 超过 1000 才裁剪到 800），把裁剪成本降低 20 倍。

**为什么是 800 条？**

| 保留条数 | 存储总量 | 覆盖浏览深度 | 判定 |
|---:|---:|---|---|
| 200 | 2.8 TB | 10 页 | ❌ 重度用户一次会话就翻完了 |
| **800** ⭐ | **11 TB** | **20~40 页** | ✅ 覆盖 **99.9%** 用户的单次会话浏览深度 |
| 3,000 | 42 TB | 150 页 | ❌ 为 0.1% 的用户付 4 倍存储成本 |

**⚠️ 那第 801 条以后怎么办？（分页的冷路径）**

```
┌─────────────────────────────────────────────────────────────────┐
│  热路径（99.9% 的请求）：cursor 落在最近 800 条内                 │
│     → ZREVRANGEBYSCORE feed:{uid} {cursor} -inf LIMIT 0 20      │
│     → P99 < 5 ms  ⚡                                            │
├─────────────────────────────────────────────────────────────────┤
│  冷路径（0.1% 的请求）：cursor 超出了 800 条的范围                │
│     → 降级为【完全 Pull】：                                      │
│        1. 取我的关注列表（200 人）                                │
│        2. 并发查每人的 user_timeline WHERE ts < cursor LIMIT 20  │
│           （Cassandra，PK=(author_id), CK=(tweet_id DESC)）      │
│        3. 归并 + 排序 + 取 20 条                                 │
│     → P99 ≈ 300~600 ms 🟡                                       │
│     → 可接受：翻到 40 页开外的用户本来就极少，且他们有耐心         │
│     → 可对这条路径单独限流，防止爬虫打爆数据库                    │
└─────────────────────────────────────────────────────────────────┘
```

> 💬 **面试话术**："这是一个典型的**热冷分离**。用 11TB 的 Redis 服务 99.9% 的请求，剩下 0.1% 用数据库慢查询兜底。如果为了那 0.1% 把 Feed 全存下来，成本要涨 500 倍。"

#### 4️⃣ 排序（Ranking）

**核心认知：真实产品的 Feed 是「召回 + 排序」两阶段架构。**

```
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│   阶段一：召回（Retrieval / Candidate Generation）                    │
│   ────────────────────────────────────────────────                   │
│   ⭐ 这一章讲的所有东西（Push / Pull / Hybrid）都属于「召回」          │
│                                                                      │
│   职责：从全站 5 亿条推文里，快速捞出「与我相关的」500~1000 条候选     │
│   要求：快（< 10ms）、全（不漏掉重要内容）、便宜                       │
│   来源：                                                              │
│     ├─ Push 路：feed:{uid} 的 Top 300           （关注的普通用户）    │
│     ├─ Pull 路：我关注的大V的最近推文 Top 300     （关注的大V）        │
│     ├─ 兴趣召回：基于 embedding 的相似内容 Top 200 （推荐，非关注）    │
│     └─ 热门召回：全站/地域热榜 Top 100                                │
│                        │                                             │
│                        ▼  ~1000 条候选（只有 tweet_id）               │
│                                                                      │
│   阶段二：排序（Ranking）—— 独立的 Ranking Service                    │
│   ────────────────────────────────────────────────                   │
│   职责：给这 1000 条候选打分，选出最好的 20 条                        │
│   ├─ 粗排（Pre-ranking）：轻量模型/线性加权，1000 → 200，耗时 ~5ms    │
│   ├─ 精排（Ranking）：GBDT / DNN 预估 CTR，200 → 50，耗时 ~30ms      │
│   └─ 重排（Re-ranking）：打散（同作者不连续）、多样性、        ~5ms   │
│                          广告插入、内容安全过滤                       │
│                        │                                             │
│                        ▼  最终 20 条 → hydrate → 返回                │
└──────────────────────────────────────────────────────────────────────┘
```

> ⭐ **面试关键点**：把 Feed 生成和排序**解耦成两个服务**。Feed Service 只负责"捞出候选"，Ranking Service 负责"排序"。这样：
> - 算法团队可以独立迭代模型，不动 Feed 基础设施
> - Ranking 挂了可以**降级为纯时间序**，Feed 依然可用（优雅降级）
> - Ranking 是 CPU/GPU 密集型，Feed 是 IO 密集型，资源模型完全不同，应该独立扩缩容

**时间序 vs 算法排序：**

| 维度 | 时间序（Chronological） | 算法排序（Ranked） |
|---|---|---|
| 实现 | ZSET 的 score 直接用时间戳 | score 由模型实时计算 |
| 计算位置 | **写时**就定好了 | **读时**计算（必须，因为分数会随时间/上下文变化） |
| 延迟 | ~1 ms | +30~50 ms |
| 用户留存 | 基线 | 通常 **+20~40%** |
| 可预测性 | ✅ 用户能理解"为什么看到这条" | ❌ 黑盒，容易招致监管/舆论质疑 |
| 一致性 | ✅ 刷新两次结果一致 | ⚠️ 分数会变，需要用 cursor + 快照保证分页不重不漏 |
| 代表 | 早期 Twitter、微信朋友圈 | 现在的 X / Instagram / Facebook / 抖音 |

**简单加权公式示例（粗排常用）：**

```python
import math, time

def compute_score(tweet, viewer) -> float:
    """粗排打分：三因子线性加权（真实系统会有几十上百个特征）"""

        # ① 新鲜度（Freshness）：指数衰减，时间常数 6 小时（半衰期 = 6·ln2 ≈ 4.2 小时）
    #    刚发的推文 ≈ 1.0，6 小时前 ≈ 0.37，24 小时前 ≈ 0.018
    age_hours = (time.time() - tweet.created_at) / 3600
    freshness = math.exp(-age_hours / 6.0)

    # ② 作者亲密度（Affinity）：我和这个作者的历史互动强度
    #    = f(点赞次数, 评论次数, 主页访问次数, 是否互关)，归一化到 [0, 1]
    #    这份数据离线算好，存在 Redis: affinity:{viewer}:{author}
    affinity = get_affinity(viewer.id, tweet.author_id)

    # ③ 互动率（Engagement）：这条推文本身的质量信号
    #    = (点赞 + 3×转发 + 5×评论) / 曝光数，做对数压缩防止头部霸榜
    raw = tweet.likes + 3 * tweet.retweets + 5 * tweet.replies
    engagement = math.log1p(raw) / math.log1p(tweet.impressions + 1)

    # 权重通过 A/B 实验调优；精排阶段这三项会被 DNN 替代
    W1, W2, W3 = 0.40, 0.35, 0.25
    return W1 * freshness + W2 * affinity + W3 * engagement
```

> ⚠️ **一个常见的设计陷阱**：不要把 ranking score 直接写进 `feed:{uid}` ZSET 的 score 字段！因为分数会随时间衰减、随互动数变化，写死在 ZSET 里就无法更新。**ZSET 的 score 永远用时间戳（也天然满足分页 cursor 的需求），ranking 在读时做。**

#### 5️⃣ 削峰与背压（Backpressure）

**为什么必须有 Kafka？**

```
峰值发帖 QPS = 5 万
峰值扇出写入（活跃过滤后）= 5 万 × 100 活跃粉丝 = 500 万 次/秒（未过滤为 5 万 × 200 = 1000 万/秒）
                        ↓
┌────────────────────────────────────────────────────────────────┐
│  ❌ 如果同步扇出（Tweet Service 直接写粉丝 Feed）：              │
│     • 发帖 API 的 P99 = 扇出完成时间 = 数秒~数分钟              │
│     • Redis 集群瞬间被打爆，影响读路径（读写共用集群时）         │
│     • Fanout 失败 → 发帖失败 → 用户体验灾难                     │
│                                                                │
│  ✅ 有 Kafka 之后：                                             │
│     • 发帖 API 只做：落库 + 发一条 Kafka 消息 → P99 < 50ms ⚡   │
│     • Kafka 承担缓冲：峰值 5 万 msg/s 对 Kafka 是小菜           │
│     • Worker 按自己的处理能力消费（背压天然形成）                │
│     • 失败自动重试（消费位点不提交）                             │
│     • 扇出逻辑变更、Worker 重启，都不影响发帖主链路              │
└────────────────────────────────────────────────────────────────┘
```

**Kafka 配置要点：**

| 配置项 | 取值 | 理由 |
|---|---|---|
| 分区键 | `author_id` | 保证**同一作者的推文有序**扇出，避免新推文排在老推文前面 |
| 分区数 | 1,000+ | 峰值 500 万写/秒，需要足够并行度；分区数 ≥ Worker 数 |
| 消费语义 | at-least-once | 配合读时 `tweet_id` 去重，实现端到端幂等 ✅ |
| 保留时长 | 3 天 | 给 Worker 故障恢复留出重放窗口 |
| Lag 告警 | > 5 分钟 | Consumer Lag 是这个系统最重要的健康指标 |

**大 V 发帖时的扇出限流（即使走 Pull 也需要）：**

虽然大 V 走 Pull 不扇出，但**次大 V**（比如 50 万粉丝，低于 100 万阈值）仍走 Push，一条推文就是 50 万次写。需要保护：

```python
# ① 大扇出任务拆分（Task Splitting）：不要一个 Worker 干 50 万次写
def plan_fanout(author_id, tweet_id):
    followers = get_active_followers(author_id)         # 假设 50 万个
    CHUNK = 1000                                        # 每个子任务 1000 个粉丝
    for i in range(0, len(followers), CHUNK):
        # 拆成 500 个子任务，投回 Kafka，由 500 个 Worker 并行消费
        # 单个子任务耗时 ≈ 1000 次 Pipeline ZADD ≈ 20ms
        kafka.send("fanout_task", {
            "tweet_id": tweet_id,
            "targets": followers[i:i + CHUNK],
        })

# ② 单作者扇出速率限流（令牌桶）：防止一个大账号占满全部 Worker
RATE_LIMIT_PER_AUTHOR = 100_000     # 每个作者每秒最多 10 万次扇出写

# ③ 全局背压：监控 Redis 写延迟，超阈值则主动降速
def adaptive_throttle():
    p99 = metrics.get("redis.zadd.p99_ms")
    if p99 > 10:
        # Redis 吃力了 → 暂停 P1（低优）消费组，只保 P0
        pause_consumer_group("fanout_p1")
    elif p99 < 3:
        resume_consumer_group("fanout_p1")
```

**熔断与降级预案：**

| 故障 | 降级动作 | 用户感知 |
|---|---|---|
| Kafka Lag > 10 分钟 | 暂停 P1 消费组，只扇 P0（在线用户） | 离线用户 Feed 延迟，无感 |
| Redis Feed Cache 挂了 | 读路径全部降级为**完全 Pull** | 延迟从 100ms 涨到 600ms，但**服务不中断** ✅ |
| Ranking Service 超时 | 降级为**纯时间序** | 内容质量下降，功能正常 ✅ |
| Cassandra 慢 | hydrate 只返回缓存命中的，miss 的跳过 | Feed 少几条，可接受 |

> 💬 **面试话术**："Hybrid 的一个隐藏优势是**它天然自带降级方案**。因为系统里同时实现了 Push 和 Pull 两条链路，Redis 挂了可以整体退到完全 Pull，扇出挂了可以退到实时计算。这是纯 Push 架构做不到的——纯 Push 的 Redis 一挂，Feed 就是一片空白。"

---

### 完整对比表

| 维度 | Push（Fan-out on Write） | Pull（Fan-out on Read） | ⭐ **Hybrid** |
|---|---|---|---|
| **写延迟**（发帖 API） | 🟡 异步后 < 50ms，但扇出完成需秒~分钟级；大V 可达小时级 ❌ | ✅ **< 20ms**，只写自己的 timeline，O(1) | ✅ **< 50ms**，普通用户异步扇出，大V 只写 1 次 |
| **读延迟**（Feed P99） | ✅ **< 50ms**，一次 ZREVRANGE 搞定 | ❌ **500ms ~ 2s**，需并发查 200 个人的 timeline + 归并排序 | ✅ **< 100ms**（1 次 ZSET + 1 次 Pipeline 并发） |
| **写放大** | ❌ 极高：平均 **1:100**，头部账号 **1:1 亿** | ✅ **1:1**，无放大 | 🟢 平均 **1:50**（活跃过滤后），大V **1:1** |
| **读放大** | ✅ **1:1**，一次查询 | ❌ **1:200**（关注数），且是**扇出查询**（尾延迟被最慢的那个决定） | 🟢 **1:2**（1 次 Push 查询 + 1 次 Pipeline，无论关注多少大V） |
| **存储成本** | ❌ **~11 TB**（2 亿 × 800 条 × 70B），且是**冗余副本** | ✅ **~50 GB**，每条推文只存一份 | 🟡 **~11 TB**（Push 侧）+ 0.6 GB（Pull 侧）；活跃过滤后可降到 **~6 TB** |
| **一致性** | 🟡 最终一致，扇出延迟决定"新帖多久可见"（P99 5s~5min） | ✅ **强一致**，读的永远是最新数据 | 🟡 最终一致（普通用户）+ 强一致（大V，Pull 是实时的）|
| **大 V 问题（Celebrity）** | ❌ **致命**：1 亿粉丝 = 1 亿次写 + 7GB 存储/条，扇出需数小时 | ✅ **天然免疫**，粉丝多少与写侧无关 | ✅ **已解决**：大V 走 Pull，共享一份 timeline |
| **关注变更成本** | ❌ **高**：新关注需回填历史推文（拉对方 timeline 合并进我的 feed）；取关需清洗我的 feed（`ZREM` 该作者的所有推文，需扫全表） | ✅ **零成本**，关注列表变了，下次读自然生效 | 🟡 **中**：普通用户关注/取关需回填/清洗；关注/取关大V 是零成本 |
| **实现复杂度** | 🟡 **中**：扇出服务 + 消息队列 + 重试 | ✅ **低**：一个归并查询就行，最容易上线 | ❌ **高**：两套链路 + 判定规则 + 去重 + 状态切换 + 降级预案 |
| **运维复杂度** | 🟡 中（监控 Kafka Lag） | ✅ 低 | ❌ 高（阈值调优、缓存分层、双链路监控） |
| **适用规模** | 粉丝数分布**均匀**、上限可控的产品<br>（微信朋友圈 ≤ 5000 好友） | **早期产品 / MVP**，DAU < 100 万，或读写比接近 1:1 | **大规模社交产品**，DAU > 1000 万，存在幂律粉丝分布 |
| **一句话** | 空间换时间，写时算好 | 时间换空间，读时现算 | ⭐ **按成本分类，各走各路** |

**决策树（面试时可以直接画）：**

```
                     开始设计 Feed 系统
                            │
              ┌─────────────┴──────────────┐
              │  粉丝数有硬上限吗？          │
              └─────┬────────────────┬─────┘
              有上限 │                │ 无上限（幂律分布）
         (如 ≤5000) │                │
                    ▼                ▼
            ┌───────────────┐   ┌──────────────────────┐
            │ ✅ 纯 Push     │   │  DAU 规模？           │
            │ (微信朋友圈)   │   └──┬───────────────┬───┘
            └───────────────┘  <100万│          >1000万│
                                     ▼                 ▼
                            ┌────────────────┐  ┌─────────────────┐
                            │ ✅ 纯 Pull      │  │ ⭐ Hybrid        │
                            │ MVP 先跑起来    │  │ 生产标准答案     │
                            │ 简单、够用      │  │ (Twitter/微博)   │
                            └────────────────┘  └─────────────────┘
```

---

### 各家怎么做的（实战案例）

| 产品 | 方案 | 关键实现 | ⭐ 为什么这么选（与产品形态强相关） |
|---|---|---|---|
| **Twitter / X** | **Hybrid**：Push 为主 + 大V Pull | Timeline 存 **Redis**（自研 fanout 服务 + Earlybird 搜索索引）；每个用户 Timeline 保留约 **800 条**；三副本跨机房 | 单向关注 + 幂律粉丝分布（头部 1 亿粉丝）→ 纯 Push 必死于 Celebrity Problem。Twitter 是"Hybrid"这个词的**发源地**，早期演讲里明确说：粉丝超阈值的账号不扇出，读时合并 |
| **Instagram** | **Push 为主** + **Cassandra** 持久化 | 用 Cassandra 存 Feed（而非纯 Redis），配合 Redis 做热缓存；Django + Celery 做异步扇出；后期加入 ML 排序 | 图片社交，**内容量远小于 Twitter**（用户发帖频率低），扇出压力可控。选 Cassandra 是因为**写吞吐极高 + 线性扩展 + 天然按 user_id 分区**，比全内存 Redis 便宜一个数量级 |
| **Facebook** | **Pull 为主 + 聚合（Aggregator）** | 不预生成 Feed，读时由 **Aggregator** 实时从各 Leaf 节点拉取候选，再交给排序层；重度依赖 **TAO**（图存储）+ 多级缓存 | 因为 News Feed 是**重排序**产品——排序信号（好友互动、广告、群组、页面）实时变化，**预计算的 Feed 立刻就过期了**。既然最终都要重排，不如读时现拉。产品形态决定了预计算没有价值 |
| **LinkedIn** | **Hybrid** | 基于 **Kafka**（LinkedIn 自研）+ Samza 流处理做扇出；FollowFeed 系统同时支持写时物化和读时聚合 | 职业社交，用户既关注人也关注公司/话题，**关注对象类型多样**；同时存在"影响力大 V"（如行业领袖，百万粉丝）→ 必须 Hybrid |
| **微信朋友圈** | **纯 Push**（读扩散/写扩散均可，实际偏写扩散+读校验） | **好友数硬上限 5000**；数据存自研 KV；发布后写入好友的时间线索引 | ⭐ **最关键的差异：好友数有 5000 硬上限，且是双向好友关系（非单向关注）**。这意味着**扇出上限恒定为 5000**，Celebrity Problem 从根上不存在！所以完全不需要 Hybrid。这是**用产品设计规避技术复杂度**的经典案例 |
| **微博** | **Push + Pull 混合**（推拉结合） | 明确的推拉结合策略：普通用户写扩散，大V（千万级粉丝）读扩散；活跃用户优先推送，非活跃用户不推；Redis 集群存 Timeline | 中文社交里 Celebrity Problem 最极端的场景（顶流明星粉丝数亿，且发帖会引发**读峰值 10 倍暴涨**）。除了推拉结合，还额外做了**热点事件的多级缓存和降级预案**（著名的"明星塌房宕机"驱动的架构演进） |

#### ⭐ 提炼：三个决定架构选择的产品因素

```
┌──────────────────────────────────────────────────────────────────────┐
│  因素 ①：关注关系有没有【上限】？                                     │
│    有上限（微信 5000 好友）  → 扇出成本恒定 → ✅ 纯 Push 就够了        │
│    无上限（Twitter/微博）    → 幂律分布     → ⭐ 必须 Hybrid          │
├──────────────────────────────────────────────────────────────────────┤
│  因素 ②：是【单向关注】还是【双向好友】？                              │
│    双向好友 → 关系对称，粉丝数 = 好友数，分布均匀 → Push 友好          │
│    单向关注 → 可以有 1 亿粉丝而只关注 10 人 → 极端不对称 → 需要 Pull   │
├──────────────────────────────────────────────────────────────────────┤
│  因素 ③：是【时间序】还是【算法排序】？                                │
│    时间序 → 顺序写时就定了 → ✅ 预计算（Push）价值最大                 │
│    算法排序 → 分数读时才知道 → 预计算的顺序会失效 → Pull/聚合更合理    │
│              （这就是 Facebook 选 Pull 的根本原因）                    │
└──────────────────────────────────────────────────────────────────────┘
```

> 💬 **面试收尾话术（强烈建议这样说）**：
> "所以我的结论是：**没有放之四海皆准的 Feed 架构，架构选择是产品形态的函数。**
> 对于我们这个类 Twitter 的场景——2 亿 DAU、单向关注、无粉丝上限、存在 1 亿粉丝的头部账号、读写比 60:1——我会选 **Hybrid**：
> 1. **粉丝 < 100 万的用户走 Push**，只扇给近 7 天活跃的粉丝，按在线状态分级扇出，每个 Feed 截断到 800 条；
> 2. **粉丝 > 100 万的 1 万个大V 走 Pull**，全站共享一份 timeline，总共不到 1GB，可以全量本地缓存；
> 3. **读时归并两路，按 tweet_id 去重**，再交给独立的 Ranking Service 排序。
>
> 这套组合把峰值写 QPS 从不可行的 4,000 万降到 240 万，读 P99 控制在 100ms 以内，同时因为两条链路都在，**任何一条挂了都能降级到另一条**。
> 如果面试官问'能不能更简单'，我会说：**MVP 阶段先上纯 Pull，DAU 过百万再加 Push，粉丝出现头部效应再拆出 Hybrid**——架构应该跟着规模演进，而不是一上来就上最复杂的。"

---

## 八、深入探讨（Deep Dives）

> 💡 **本章定位**：前面章节解决了"Push / Pull / 混合"的主干选型。这一章处理的是面试官在白板画完架构图后必然会追问的那一堆"那如果……怎么办"。
> Senior 和 Staff 的分水岭通常不在于能不能画出 Fan-out on Write，而在于能不能把**取关、删帖、缓存崩了、大 V 粉丝列表放不下、分页重复**这些脏活的取舍讲清楚。

**全章统一口径（不再重复推导）：**

| 指标 | 数值 |
|---|---|
| DAU | 2 亿 |
| 平均关注数 / 粉丝数 | 200 / 200（长尾分布） |
| 大 V（粉丝 > 100 万） | 约 1 万个账号，头部 1 亿+ 粉丝 |
| 发帖量 | 5 亿/天 → 写 QPS ≈ 6,000，峰值 ≈ 50,000 |
| 读 Feed | 300 亿/天 → 读 QPS ≈ 350,000，峰值 ≈ 1,000,000 |
| 读写比 | ≈ 60 : 1 |
| Feed 首屏 | 20 条，游标分页 |
| SLO | Feed 加载 **P99 < 200ms** |

---

### 1. Feed 数据该存在哪？Redis 还是数据库？

#### 1.1 先明确存的是什么

Feed 缓存里存的**不是推文正文**，而是一条**有序的 id 列表**：

```
feed:{user_id}  →  Redis ZSET（有序集合）
   member = tweet_id（定长字符串，19 位零填充）
   score  = 发帖时间戳（毫秒）

ZSET 内容示意（保留最新 800 条）：
┌──────────────────────────────────────────────────────┐
│ 0001845123456789012  score=1755230401123   ← 最新    │
│ 0001845123456788881  score=1755230399870             │
│ 0001845123456712345  score=1755230388012             │
│ ...                                                  │
│ 0001845098765432100  score=1755143210000   ← 第800条 │
└──────────────────────────────────────────────────────┘
       ↑ 只有 id，正文在读的时候去 Tweet 缓存 hydrate
```

⭐ **面试中一定要主动说出来的一句话**："Feed 缓存是**派生数据（derived data）**，它的唯一真相来源（source of truth）是 Tweet 表 + Follow 表，任何时候都可以重算出来。" 这句话是后面所有容灾方案的地基。

#### 1.2 Redis vs Cassandra/HBase 对比

| 维度 | Redis（内存） | Cassandra / HBase（磁盘） |
|---|---|---|
| 读延迟 P99 | **0.5 ~ 2 ms** ✅ | 10 ~ 50 ms（走 SSD + LSM 合并）🟡 |
| 单实例吞吐 | 8 ~ 12 万 QPS（单线程 O(logN)） | 1 ~ 3 万 QPS/节点 |
| 数据结构 | 原生 ZSET，`ZADD` / `ZREVRANGE` 天然是 Feed 语义 ✅ | 需要用 wide row（clustering key = tweet_id DESC）模拟 🟡 |
| 截断成本 | `ZREMRANGEBYRANK` O(logN + M) ✅ | 需要 TTL 或后台 compaction，删除写 tombstone ❌ |
| 持久化 | RDB/AOF 有窗口，主从切换可能丢秒级数据 ⚠️ | 多副本 + Quorum，真持久 ✅ |
| 成本 | **$4~5 / GB / 月** ❌ | **$0.1~0.3 / GB / 月**（gp3 云盘）✅ |
| 扩容 | Cluster resharding，slot 迁移有抖动 🟡 | 加节点自动 rebalance ✅ |
| 崩了的后果 | 全量 miss → 重建风暴 ⚠️ | 副本顶上，几乎无感 ✅ |

#### 1.3 生产做法：热数据在 Redis，冷数据兜底

```
                          读 Feed 请求
                              │
                              ▼
                   ┌──────────────────────┐
              命中 │  Redis ZSET 热层      │  99.5% 命中
        ┌──────────│  最近 800 条 / 用户   │
        │          │  仅活跃用户（8000万） │
        │          └──────────┬───────────┘
        │                     │ miss (0.5%)
        │                     ▼
        │          ┌──────────────────────┐
        │          │ 冷路径：实时重算       │
        │          │ following(200人)      │
        │          │  → user_timeline 归并 │
        │          │  → 回填 Redis         │
        │          └──────────┬───────────┘
        │                     │
        ▼                     ▼
   hydrate（Tweet 正文缓存）→ 返回 20 条
```

**为什么是 800 条？**

| 位置 | 数据 |
|---|---|
| 首屏 | 20 条 |
| 用户单次会话平均下拉 | 2~3 屏（40~60 条） |
| P99 用户下拉 | 10 屏（200 条） |
| 800 条 | 覆盖 **99.9%** 的会话；翻过 800 条的用户直接走 Pull 实时归并（占比 < 0.1%，成本可接受） |

> 💡 **核心思想**：缓存的容量应该由**用户行为分布的 P99.9**决定，而不是拍脑袋。多存一倍是线性烧钱，少存一倍是长尾体验崩塌。

#### 1.4 内存成本估算（面试必算）

**单条 entry 的真实内存开销**（这是很多人算错的地方 ⚠️）：

```
裸数据：tweet_id 8B + score 8B = 16 B
但 Redis ZSET 超过 zset-max-listpack-entries(128) 会转成 skiplist 编码：
  - dictEntry（member → score 的哈希表项）  ≈ 48 B
  - zskiplistNode（含平均 1.33 层指针）      ≈ 40 B
  - sds（member 字符串，19 字节 + header）   ≈ 32 B
  - 内存分配器碎片（jemalloc）                ≈ 10%
  ────────────────────────────────────────────
  - dictEntry（member → score 的哈希表项）  ≈ 24 B
  - zskiplistNode（score + 前后指针，平均 1.33 层）≈ 24 B
  - sds（member 定长短串 + header）          ≈ 10 B
  - 内存分配器碎片（jemalloc）                ≈ 10%
  ────────────────────────────────────────────
  实际 ≈ (24 + 24 + 10) × 1.1 ≈ 64 B / 条（约为裸数据的 4 倍）
```

| 项 | 计算 | 结果 |
|---|---|---|
| 单用户 Feed | 800 × 64 B | **≈ 50 KB** |
| 全量 2 亿用户 | 50 KB × 2e8 | **10 TB** ❌ 太贵 |
| 只缓存 7 日活跃用户（约 40% = 8000 万） | 50 KB × 8e7 | **≈ 4 TB** ✅ |

**4 TB 需要多少机器？**

| 项 | 数值 |
|---|---|
| 单实例机型 | r6g.8xlarge，256 GB 内存 |
| 有效可用率 | 60~65%（要给 RDB fork 的 copy-on-write、碎片、突发留余量） |
| 单机有效容量 | ≈ 160 GB |
| 主节点数 | 4 TB / 160 GB ≈ **25 个分片** |
| 加 1 副本 | **50 台机器** |
| 云上 On-Demand | ≈ $1,180 / 台 / 月 → **≈ $5.9 万 / 月 ≈ $71 万 / 年** |
| 预留实例 / 自建 IDC | 打 3~4 折 → **≈ $20~25 万 / 年** |

对照：同样 4 TB 数据放 Cassandra（3 副本 = 12 TB SSD），云盘成本约 **$1,500 / 月**，差了 **约 40 倍**。

> 💡 **核心思想**：内存是"用 40 倍的钱换 20 倍的延迟"。所以只有**被高频读的热数据**才配住在内存里 —— 8000 万活跃用户承担了 300 亿次/天读的 99%+。

**面试中怎么说**："我不会给全部 2 亿用户建 Feed 缓存。做法是**懒建 + LRU 淘汰**：用户登录/首次拉 Feed 时才建，超过 7 天不活跃自然被淘汰。这把 10 TB 压到 4 TB，省掉 60% 的钱，代价只是不活跃用户回归时首刷慢 200ms —— 这个交易稳赚。"

#### 1.5 ⚠️ 一个必踩的坑：ZSET score 精度

**不能把 Snowflake ID 直接当 score！**

```
Redis ZSET 的 score 是 IEEE 754 double，尾数只有 53 位有效精度。

Snowflake ID ≈ 1.7 × 10^18  ≈ 2^60.6
double 精确整数上限 = 2^53 = 9.007 × 10^15

→ 超出部分被舍入，末尾约 2^(61-53) = 256 ~ 1024 的粒度被抹平
→ 同一毫秒内同一台机器的 4096 个连续序列号被压缩到只剩 16 个可区分档位（每 256 个相邻 ID 塌缩成同一个 score）
→ 排序错乱 + 游标分页错位（会漏帖或重复）  ❌
```

✅ **正确做法**：

```bash
# score 用毫秒时间戳（相对自定义 epoch，值 ≈ 5×10^11，远小于 2^53，精确）
# member 用完整 tweet_id 的【定长零填充字符串】
#   → 同 score 时 Redis 按 member 字典序排，定长零填充保证字典序 == 数值序
ZADD feed:u1001 1755230401123 "0001845123456789012"

# 截断：只保留最新 800 条（rank 0 是最小 score，-801 往前的全删）
ZREMRANGEBYRANK feed:u1001 0 -801

# 首屏：取最新 30 条（over-fetch，见 §3）
ZREVRANGE feed:u1001 0 29
# Redis 6.2+ 等价写法
ZRANGE feed:u1001 +inf -inf BYSCORE REV LIMIT 0 30
```

#### 1.6 Redis 挂了怎么办？—— 重建风暴（Cache Stampede）

**先说结论**：Feed 缓存丢失**不丢数据**，只丢性能。但如果不加保护，恢复过程会**打死后端**。

**算一下风暴有多大：**

| 步骤 | 计算 | 结果 |
|---|---|---|
| 缓存全挂后，峰值读 QPS | — | 100 万 |
| 每次 miss 需要重建：查 200 个关注者的 user_timeline | 100 万 × 200 | **2 亿 QPS** 打到 Tweet 存储 |
| Tweet 存储集群设计容量 | — | 约 500 万 QPS |
| 超载倍数 | 2e8 / 5e6 | **40 倍** ❌ 必然雪崩 |

**四道防线：**

```
请求 → ① 单飞(singleflight) → ② 全局重建限流 → ③ 降级重建 → ④ 分级预热
        同一 user_id 的并发       令牌桶 1万/s      只归并 Top-20     后台按活跃度
        重建请求只放行 1 个        超出直接降级       关注者 + 大V      排队预热
```

| 防线 | 机制 | 效果 |
|---|---|---|
| ① **Singleflight** | 同一 `user_id` 的并发重建在进程内合并；跨进程用 `SET rebuild:lock:{uid} NX EX 5` | 用户狂刷新时 N 次重建 → 1 次 |
| ② **重建限流** | 独立令牌桶，全局 **1 万 rebuild/s**（→ 后端 200 万 QPS，在 500 万容量内 ✅） | 后端永不被打穿 |
| ③ **降级重建** | 超限时走"极简 Feed"：只归并**互动最多的 20 个关注者** + 大 V 一路 | 单次成本从 200 次查询降到 **20 次（省 10 倍）**，Feed 内容少但可用 🟡 |
| ④ **分级预热** | 只重建**当前在线**用户（约 500 万），不是全量 8000 万；按最近活跃时间排序 | 500 万 / 1 万每秒 = **500 秒 ≈ 8 分钟**恢复 ✅ |

```python
def get_feed(user_id, cursor=None, limit=20):
    ids = redis.zrevrangebyscore(f"feed:{user_id}", cursor or "+inf", "-inf",
                                 start=0, num=limit + 10)   # over-fetch 见 §3
    if ids:
        return hydrate(user_id, ids, limit)

    # ---- 缓存 miss，进入重建路径 ----
    if not rebuild_limiter.try_acquire():                    # ② 全局令牌桶 1万/s
        return degraded_feed(user_id, limit)                 # ③ 降级：Top-20 关注者 + 大V

    lock = redis.set(f"rebuild:lock:{user_id}", "1", nx=True, ex=5)   # ① 跨进程单飞
    if not lock:
        time.sleep(0.05)                                     # 让先到的那个请求去建
        return get_feed(user_id, cursor, limit)              # 重试读缓存

    ids = rebuild_feed(user_id)        # 归并 200 个 following 的 user_timeline，回填 ZSET
    return hydrate(user_id, ids, limit)
```

> 💡 **核心思想**：对派生数据来说，**"缓存能重建"不等于"缓存可以随便丢"**。重建的代价必须被限流器显式定价，否则一次 Redis 主从切换就是一次全站故障。

**⚠️ 面试加分点**：Redis Cluster 建议**分批重启 / 分 slot 迁移**，永远不要同时重启超过 1/4 的分片；同时保留 Cassandra 里的 `feed_snapshot` 表（每天为高价值用户落一份 200 条快照，成本约 $500/月），让重建先读快照再增量补齐，把单次重建成本从 200 次查询降到 **1 次 + 增量**。

---

### 2. 关注 / 取关时 Feed 怎么处理（Backfill 问题）

#### 2.1 问题定义

用户 A 关注了 B。此时 A 的 `feed:A` 里**一条 B 的帖子都没有**（因为扇出只对关注建立**之后**的新帖生效）。
如果 B 一周才发一次帖，A 可能关注后好几天都感觉"这人像不存在"。❌ 体验很差。

#### 2.2 三种方案对比

| 方案 | 做法 | 写放大 | 读延迟 | 一致性 | 复杂度 | 评价 |
|---|---|---|---|---|---|---|
| **A. 立即 Backfill** | 关注时把 B 最近 N 条（如 50 条）`ZADD` 进 `feed:A`，再截断到 800 | 高：单次关注 = 50 次 ZADD + 1 次 user_timeline 查询 | 无影响 ✅ | 好 ✅ | 中 | 🟡 关注是低频操作时可行 |
| **B. 不 Backfill** | 什么都不做，只对之后的新帖生效 | 0 ✅ | 无影响 ✅ | 差 ❌（刚关注时看不到人） | 低 ✅ | ❌ 体验不可接受 |
| **C. 读时按 Pull 处理** | 把"最近 T 小时内新关注的人"记入 `recent_follows:A`，读 Feed 时对这些人做实时 Pull 归并，T 小时后自然过期 | 0 ✅ | +5~15ms 🟡 | 好 ✅ | 中高 | ⭐ **推荐** |

**关注操作的量级**：DAU 2 亿，人均每天关注约 0.5 次 → **1 亿次/天 ≈ 1,200 QPS，峰值约 1 万 QPS**。

- 方案 A 在峰值下产生 `1万 × 50 = 50 万 ZADD/s`，虽然不至于打死 Redis（50 个分片各 1 万/s），但会和正常扇出抢带宽。
- 大 V 被关注是热点：某个明星发新歌，1 小时内被关注 500 万次 → 500 万 × 50 = **2.5 亿次 ZADD** ❌ 这就是方案 A 的杀手场景。

#### 2.3 推荐组合：C 为主 + A 的限流版本

```
关注 A → B
   │
   ├─ 写 following:A / followers:B（真相来源，必须同步成功）
   │
   ├─ ZADD recent_follows:A  score=now  member=B   （TTL 24 小时）
   │        ↑ 读 Feed 时对这批人做 Pull 归并
   │
   └─ 异步任务：若 B 的粉丝数 < 100万（非大V）
          → 拉 B 最近 20 条，ZADD 进 feed:A（限流 5000 QPS 全局）
          → 完成后从 recent_follows:A 中移除 B
```

| 用户身份 | 处理 |
|---|---|
| B 是普通用户（99.99% 情况） | 异步 backfill 20 条，秒级完成，之后走纯 Push ✅ |
| B 是大 V | **不 backfill**（大 V 本来就走 Pull 路线，读时归并天然覆盖）✅ |
| 批量导入关注（新用户 onboarding 一次关注 30 人） | 全部走 `recent_follows` 的 Pull 路线，避免 30 × 20 = 600 次 ZADD 🟡 |

#### 2.4 取关：写时清理 vs 读时过滤 ⭐

A 取关 B，`feed:A` 里还躺着 B 的 50 条帖子，怎么办？

**方案 X：写时清理（从 ZSET 中删除）—— 很贵**

```
问题：feed:A 的 member 只有 tweet_id，【不知道作者是谁】！
  → 必须 ZRANGE 全部 800 条
  → 800 次 tweet → author 的反查（哪怕批量 MGET 也要走缓存 800 个 key）
  → 找出属于 B 的，ZREM 掉
成本：单次取关 ≈ 800 次读 + N 次写，延迟 20~50ms
风险：用户"清粉"批量取关 200 人 → 200 × 800 = 16 万次查询  ❌
```

即使把 member 改成 `{author_id}:{tweet_id}` 编码（本地就能判作者），仍需 **O(800) 全量扫描 + 反序列化**，且 member 变长会让内存从 64B/条涨到 ~80B/条（4 TB → 5 TB，**多花 $18 万/年**）。

**方案 Y：读时过滤（推荐）⭐**

```python
def hydrate(user_id, tweet_ids, limit):
    tweets = tweet_cache.mget(tweet_ids)                  # ① 批量取正文（1 次 RTT）
    tweets = [t for t in tweets if t is not None]         # ② 已删除的直接消失（见 §3）

    authors = [t.author_id for t in tweets]
    # ③ 一次 SMISMEMBER 批量校验：这些作者是否【仍在】我的关注列表里
    still_following = redis.smismember(f"following:{user_id}", authors)
    # ④ 一次 SMISMEMBER 批量校验：是否被我拉黑 / 是否把我拉黑
    blocked = redis.smismember(f"blocklist:{user_id}", authors)

    out = []
    for t, ok, blk in zip(tweets, still_following, blocked):
        if not ok or blk:      continue                   # 已取关 / 已拉黑 → 跳过
                if t.visibility == "private" and not is_follower_of(t.author_id, user_id):
            continue                                      # 私密账号：viewer 必须是作者的粉丝
        out.append(t)
        if len(out) == limit: break                       # 够 20 条就停
    return out
```

| 维度 | 写时清理 | 读时过滤 ⭐ |
|---|---|---|
| 取关延迟 | 20~50 ms ❌ | **0 ms**（只删 following 表）✅ |
| 取关成本 | 800 次读/次 ❌ | 0 ✅ |
| 读路径额外成本 | 0 | **+1 次 SMISMEMBER（≈ 0.3ms）**，200 个元素的 SET 只占 ~8KB 🟡 |
| 脏数据窗口 | 无 | 无（读时永远看的是最新关注关系）✅ |
| 批量取关 | 灾难 ❌ | 无感 ✅ |
| 实现复杂度 | 高（要存 author，要扫全表） | 低 ✅ |

**成本对照**：取关 QPS ≈ 300（远低于读的 100 万），但**写时清理的单次成本是读时过滤的约 800 倍**（800 次反查 vs 1 次批量 SMISMEMBER；按延迟算是 20~50ms vs 0.3ms，约 70~170 倍）。而读时过滤把成本摊到 100 万 QPS × 0.3ms，用的是一次本来就要发的 pipeline 往返。

> 💡 **核心思想（本章最重要的一条原则）**：
> **在读多写少的系统里，把"可见性判定"放在读路径，而不是写路径。**
> 写时清理是「主动去所有副本上擦除」—— 副本有多少份，成本就翻多少倍，且永远擦不干净（时序竞态）；
> 读时过滤是「在唯一的出口处做一次判定」—— 成本恒定，结果永远正确。
> 这套逻辑适用于：**取关、拉黑、删帖、设私密、封号、地域屏蔽、内容降权**，全部同构。

#### 2.5 各种可见性变更的统一处理表

| 事件 | 写时要做的（必须） | 读时过滤的（推荐） | 为什么不写时清理 |
|---|---|---|---|
| 取关 | 删 `following` / `followers` | `SMISMEMBER following` | 需扫 800 条 |
| 拉黑 | 写 `blocklist` 双向 | `SMISMEMBER blocklist` | 要清双方的 Feed，2×800 |
| 删帖 | Tweet 表打 `deleted` 标记，删缓存 | hydrate 返回 null → 跳过 | 帖子在 **粉丝数** 份 Feed 里，大 V 就是 1 亿份 ❌ |
| 设为私密 | 改 `visibility` 字段 | 校验 viewer ∈ 粉丝 | 同上 |
| 账号封禁 | 打 `suspended` 标记 | hydrate 时过滤 | 该账号所有帖 × 所有粉丝 ❌❌ |
| 地域/年龄合规 | 打标签 | 按 viewer 属性过滤 | 同一条帖对不同人可见性不同，写时根本无法预计算 |

⚠️ **唯一必须写时清理的例外**：法律强制删除（GDPR 被遗忘权、DMCA）要求数据**物理消失**。这时才跑异步清理任务，走离线批处理，不占在线链路。

---

### 3. 删帖与编辑怎么同步到 Feed？

#### 3.1 "只存 id 不存正文"的红利在这里兑现

```
如果 Feed 存正文（反范式化）：
  用户删一条帖 → 要去 200 份（大V 是 1 亿份）Feed 里逐个删除/更新
  用户编辑一条帖 → 同上，还要保证 1 亿份的一致性
  → 写放大 = 粉丝数，且是【删除/更新】这种最慢的操作          ❌❌❌

如果 Feed 只存 id：
  用户删一条帖 → 只需 UPDATE tweets SET deleted=1 WHERE id=?
                 + DEL tweet_cache:{id}
  → 写放大 = 1。Feed 里的那个 id 变成"悬垂指针"，hydrate 时自然消失  ✅
```

| 操作 | 存正文的成本 | 只存 id 的成本 |
|---|---|---|
| 删帖（普通用户，200 粉丝） | 200 次 ZSET 修改 | **1 次 DB 写 + 1 次 DEL** ✅ |
| 删帖（头部大 V，1 亿粉丝） | 1 亿次修改，跑几小时 ❌ | **1 次 DB 写 + 1 次 DEL** ✅ |
| 编辑帖子 | 1 亿次覆盖，且期间不一致 ❌ | 0（下次 hydrate 自动读到新版本）✅ |
| 点赞数/转发数变化（高频！） | 完全不可能 ❌ | 0（计数器独立缓存，hydrate 时合并）✅ |

> 💡 **核心思想**：Feed 存 id 而不是正文，本质是**用一次读时 join 换掉了写时的 N 份拷贝**。
> 它让「一条推文的所有可变状态」（正文、计数、可见性、作者昵称头像）都只有**一个副本**，永远不会出现"1 亿份 Feed 里有 3 万份还显示旧内容"的一致性噩梦。
> 代价是每次读多一次 `MGET`（约 0.5ms，命中率 > 99%）—— 完全值。

#### 3.2 hydrate 的四层过滤

```
ZREVRANGE 取出 30 个 id
        │
        ▼
  ① MGET tweet_cache  ────→ 返回 null 的 = 已删除 / 已过期，丢弃
        │
        ▼
  ② 作者状态过滤    ────→ suspended / deactivated，丢弃
        │
        ▼
  ③ 关系过滤        ────→ 已取关 / 已拉黑 / 私密不可见，丢弃
        │
        ▼
  ④ 内容策略过滤    ────→ 敏感内容、地域合规、用户屏蔽词，丢弃
        │
        ▼
   剩余 ≥ 20 条？ ──否──→ 用最后一条的 id 作游标，再取一页（最多重试 2 次）
        │是
        ▼
   截取前 20 条返回
```

#### 3.3 Over-fetch：取 20 条要取多少？

设单条被过滤掉的概率 `p`。生产实测 `p ≈ 5% ~ 12%`（删帖 2%、取关残留 4%、拉黑/封号 1%、策略过滤 3%）。取 **p = 10%**。

| 取 N 条 | 期望剩余 | 标准差 σ=√(N·p·(1-p)) | 不足 20 条的概率 |
|---|---|---|---|
| 20 | 18.0 | 1.34 | ≈ **88%**（精确值 1−0.9²⁰ = 87.8%）❌ 几乎必然不够 |
| 24 | 21.6 | 1.47 | ≈ **8.5%**（二项精确值 P(X≥5), X~B(24, 0.1)）🟡 |
| 24 | 21.6 | 1.47 | ≈ 14% 🟡 |
| **30** | **27.0** | **1.64** | (27-20)/1.64 ≈ **4.3σ → < 0.01%** ✅ |
| 40 | 36.0 | 1.90 | < 0.0001%，但多花 33% 的 hydrate 成本 |

✅ **取 30 条（1.5x over-fetch）是最优点**：额外成本仅仅是 MGET 多取 10 个 key（+0.1ms、+3KB 带宽），换来 99.99% 的一次成功率。

```python
OVER_FETCH_RATIO = 1.5      # 经验值：过滤率 10% 时，1.5x 能覆盖 4σ
MAX_RETRY = 2               # 极端情况（用户刚清粉 100 人）最多再取两页

def read_feed(user_id, cursor, limit=20):
    result, retry = [], 0
    while len(result) < limit and retry <= MAX_RETRY:
        n = int((limit - len(result)) * OVER_FETCH_RATIO) + 5     # 多取一点
        entries = redis.zrevrangebyscore(f"feed:{user_id}",
                                         max=cursor or "+inf", min="-inf",
                                         start=0, num=n, withscores=True)  # ⭐ 必须带 score
        if not entries:
            break                                                  # 到底了
        ids = [member for member, _ in entries]
        result += hydrate(user_id, ids, limit - len(result))        # 四层过滤
        # ⚠️ 游标取【score = 毫秒时间戳】，不能取 member（零填充 tweet_id ≈ 1.8e18，
        #    当成 max 传回去等价于 +inf，第二轮会把第一页原样重复返回）
        cursor = "(" + str(int(entries[-1][1]))   # 下一页游标 = 本页最后一条的 score（开区间）
        retry += 1
    return result[:limit], cursor
```

⚠️ **面试细节**：过滤发生在**服务端**，所以返回给客户端的游标必须是**过滤前 ZSET 里最后一个元素的 score**，而不是过滤后结果的最后一条 —— 否则被过滤掉的那几条会在下一页被重新扫描（性能浪费）或者跳过一段（漏帖）。

---

### 4. 长尾延迟与超时降级

#### 4.1 扇出读（scatter-gather）的尾延迟放大原理

Pull 路线下，读一次 Feed 要并发查 200 个关注者的 `user_timeline`（批量后约 20~50 次跨分片调用）。**尾延迟会被放大**：

```
设单次子调用的延迟分布：P50 = 1ms，P99 = 10ms
"这次请求慢"当且仅当【至少有一个子调用慢】

扇出 N 次后，至少一次落到 P99 尾部的概率：
       P(至少一次慢) = 1 - (1 - 0.01)^N = 1 - 0.99^N

N=10   → 1 - 0.99^10  = 1 - 0.904 =  9.6%
N=50   → 1 - 0.99^50  = 1 - 0.605 = 39.5%
N=100  → 1 - 0.99^100 = 1 - 0.366 = 63.4%   ⚠️
N=200  → 1 - 0.99^200 = 1 - 0.134 = 86.6%   ❌
```

> ⚠️ **反直觉的结论**：**单个组件 P99 = 10ms 的系统，扇出 100 次之后，整体请求有 63% 的概率至少碰上一次 10ms。**
> 也就是说：**下游的 P99 变成了上游的 P50。** 这就是 Jeff Dean《The Tail at Scale》说的尾延迟放大。

| 扇出数 N | 至少一次慢的概率 | 整体延迟大致等于 |
|---|---|---|
| 1 | 1% | 下游 P50（1ms） |
| 20 | 18% | 下游 P80 |
| 100 | 63% | **下游 P99（10ms）** |
| 200 | 87% | 下游 P99.5 |

**推论**：我们的 SLO 是 Feed P99 < 200ms。如果读路径串行查 200 个关注者，即使每次只要 5ms，也是 `200 × 5ms = 1000ms` ❌ 直接爆表。所以必须并发。

#### 4.2 四把武器

```
                     读 Feed 请求（预算 200ms）
                             │
     ┌───────────────────────┼───────────────────────┐
     ▼                       ▼                       ▼
 ① 并发扇出            ② 硬超时                ④ Hedged Request
 200 → 分 20 组         每个子调用 50ms          等 P95(15ms) 没回
 并发 goroutine         全局 deadline 150ms      → 向副本发第二份
 耗时 = max 不是 sum    到点就砍                 → 取先到的，取消另一个
     │                       │                       │
     └───────────────────────┴───────────────────────┘
                             ▼
                    ③ 部分结果降级
             收到 ≥ 90% 分片 → 直接返回（缺的下拉时补）
             收到 <  90%     → 用 Redis 里的旧 Feed 兜底
```

**① 并发**

| 方式 | 200 个关注者的耗时 |
|---|---|
| 串行 | 200 × 5ms = **1000 ms** ❌ |
| 按分片分组（200 人落在约 20 个分片）+ 分片内批量 + 分片间并发 | max(20 次调用) ≈ **10~15 ms** ✅ |

⭐ 关键优化：**按分片聚合再发请求**。200 个 followee 按 `hash(user_id) % 分片数` 分组，同一分片的合并成一次 `MGET` / 一次批量 range 查询 —— 把扇出数从 **200 降到 20**，尾延迟放大概率从 87% 降到 18%。

**② 超时**

| 层级 | 超时值 | 理由 |
|---|---|---|
| 单个 Redis 调用 | 20 ms | 正常 P99 = 2ms，20ms 说明这个节点已经病了 |
| 单个存储分片查询 | 50 ms | 留出一次 GC pause 的余量 |
| 归并阶段全局 deadline | 150 ms | 给 hydrate + 序列化留 50ms，总共 200ms |
| 整个 HTTP 请求 | 200 ms | = SLO |

⚠️ 超时必须**逐级递减**（`子超时 < 父超时 - 已耗时`），否则父超时了子还在跑，白白占着连接池。

**③ 部分结果降级**

```python
async def scatter_gather(shards, deadline_ms=150):
    tasks = [query(s) for s in shards]                      # 并发发出全部分片查询
    done, pending = await asyncio.wait(tasks,
                                       timeout=deadline_ms / 1000)
    for t in pending:
        t.cancel()                                          # 超时的直接砍掉，别拖着

    coverage = len(done) / len(shards)                      # 分片覆盖率
    if coverage >= 0.9:
        metrics.incr("feed.partial_result")                 # 打点：本次是部分结果
        return merge([t.result() for t in done]), "partial" # ✅ 缺 10% 用户基本无感
    else:
        metrics.incr("feed.fallback_stale")
        return read_stale_feed_from_redis(), "stale"        # 🟡 降级：返回上次缓存的旧 Feed
```

| 覆盖率 | 动作 | 用户感知 |
|---|---|---|
| 100% | 正常返回 | 无 |
| 90~99% | 返回部分结果，标记 `partial` | 几乎无感（20 条里可能少 1~2 条冷门内容） |
| 50~90% | 返回 Redis 里的旧 Feed（可能几分钟前的） | 🟡 "怎么没刷新" |
| < 50% | 返回"只有大 V + 自己"的极简 Feed | ⚠️ 内容少，但不白屏 |
| 全挂 | 返回客户端本地缓存 + 明确错误提示 | ❌ |

**④ Hedged Request（对冲请求）** ⭐ Staff 级加分项

```
不对冲：
  t=0    发请求 → 落到一个正在 GC 的节点
  t=80ms 才返回                                  → 本次 P99 = 80ms

对冲（阈值设在下游 P95 = 15ms）：
  t=0     向副本 A 发请求
  t=15ms  A 还没回 → 向副本 B 发第二份请求
  t=17ms  B 返回 → 立即用 B 的结果，同时 cancel A
                                                 → 本次延迟 = 17ms  ✅
```

| 项 | 数值 |
|---|---|
| 对冲阈值 | 下游 P95（本例 15ms） |
| 额外流量 | 仅 **5%**（只有 5% 的请求会超过 P95） |
| 效果（Google 实测量级） | P99 从 ~100ms → ~20ms，**降低 3~5 倍** |
| ⚠️ 前提 | 请求必须**幂等**（读 Feed 天然幂等 ✅；扇出写绝不能对冲 ❌） |
| ⚠️ 保护 | 对冲请求量必须限流（如不超过总流量 10%），否则下游抖动时会触发**对冲雪崩** |

**进阶：Tied Request** —— 同时发给两个副本，并告诉对方"另一个副本是谁"，谁先开始执行就通知另一个取消。额外流量更低，但需要下游配合实现。

> 💡 **核心思想**：在扇出系统里，**你无法通过优化平均值来改善 P99，只能通过"允许放弃"来改善 P99**。
> 超时是放弃时间，部分结果是放弃完整性，对冲是放弃一点点额外资源。这三种"放弃"是尾延迟的唯一解药。

---

### 5. 一致性模型

#### 5.1 Feed 是最终一致的，而且这完全 OK

| 场景 | 可接受延迟 | 实际延迟 |
|---|---|---|
| 普通用户发帖 → 粉丝看到 | 几秒 | Kafka + 扇出 Worker，**P50 ≈ 1s，P99 ≈ 5s** ✅ |
| 大 V 发帖 → 1 亿粉丝全部看到 | 几十秒~几分钟 | 走 Pull 路线，**读时即时可见** ✅（这反而比 Push 更快！） |
| 关注生效 | 秒级 | `recent_follows` 立即生效 ✅ |
| 点赞数更新 | 10~60 秒 | 计数器异步聚合 ✅ |

**为什么用户能接受？** 因为 Feed 是**推荐流而非事务系统**。用户没有"上帝视角"，无法知道"本该有一条帖没显示"。这和转账余额差 5 秒是根本不同的问题。

⚠️ 但有一个例外，而且是**硬要求**：

#### 5.2 Read-Your-Own-Writes（自己发的帖自己必须立刻看到）⭐

```
用户体验的死穴：
  用户发了一条帖 → 下拉刷新 → 【看不到自己刚发的】
  → 用户认为"发失败了" → 再发一次 → 重复内容 → 更糟  ❌❌
```

这是**唯一不能最终一致**的地方。三种做法：

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A. 客户端乐观插入** | 发帖 API 返回后，客户端本地把这条帖插到 Feed 顶部 | 0 服务端成本，0 延迟 ✅ | 换设备/杀进程后消失；和服务端排序可能不一致 🟡 |
| **B. 同步 ZADD 自己** | 发帖时**同步**（不走 Kafka）先 `ZADD feed:{self}`，再异步扇出给粉丝 | 简单，跨设备一致 ✅ | 发帖路径多一次 Redis 写（+0.5ms，可忽略）✅ |
| **C. 读时 merge 自己** | 读 Feed 时，额外读 `user_timeline:{self}` 最近 20 条，和 Feed 归并 | 100% 正确，不依赖写路径 ✅ | 每次读多 1 次调用（+0.5ms）🟡 |

⭐ **生产推荐：A + B 组合**

```python
def post_tweet(user_id, content):
    tweet_id = snowflake.next_id()
    db.insert_tweet(tweet_id, user_id, content)          # ① 落库（真相来源），必须成功

    # ② 【同步】写入自己的 Feed 和自己的 timeline —— Read-your-own-writes 保障
    pipe = redis.pipeline()
    pipe.zadd(f"feed:{user_id}",           {pad(tweet_id): ts_ms(tweet_id)})
    pipe.zadd(f"user_timeline:{user_id}",  {pad(tweet_id): ts_ms(tweet_id)})
    pipe.execute()                                        # 约 0.5ms，同步等待

    # ③ 【异步】丢进 Kafka，由扇出 Worker 慢慢推给 200 个粉丝
    kafka.produce("tweet.created", {"tweet_id": tweet_id, "author": user_id})

    return tweet_id   # 此时客户端可以立即乐观插入（方案 A）
```

| 步骤 | 是否同步 | 耗时 | 失败后果 |
|---|---|---|---|
| ① 落库 | ✅ 同步 | 5~10ms | 发帖失败，返回错误 |
| ② 写自己 Feed | ✅ 同步 | 0.5ms | 降级为最终一致（可容忍，不阻塞发帖） |
| ③ Kafka | ❌ 异步 | fire-and-forget | 有 outbox 表 + 补偿任务兜底 |

⚠️ **额外的坑**：如果用户在**另一台设备**上读 Feed，而那台设备的请求被路由到了**另一个 Redis 副本**（读写分离场景），可能读到主从复制延迟前的旧数据。解决：Feed 的 ZSET 读**永远走主节点**（Redis Cluster 默认行为），或者用会话粘性（session stickiness）+ `WAIT` 命令。

#### 5.3 排序稳定性：为什么 offset 分页必然出错 ⚠️

Feed 是一个**持续从头部插入**的列表，而 offset 是「跳过前 N 个位置」的按位置寻址 —— 翻页间隔内新增 3 条，第 2 页就重复 3 条；删除 3 条，第 2 页就跳过 3 条。

必须用**游标（keyset）分页**：以上一页最后一条的 score 作开区间上界，`ZREVRANGEBYSCORE feed:{uid} (1755230388012 -inf LIMIT 0 20`，O(log N) 直接定位，与页深无关。

⚠️ **同毫秒 tie-break**：峰值 5 万 QPS 发帖意味着**同一毫秒可能有 50 条推文**，score 相同。此时靠定长零填充的 `tweet_id` 做 member 字典序兜底，保证边界唯一；游标本身以 base64 不透明串返回，客户端不解析。

📖 重复 / 跳过的完整时间轴推演、Offset vs Cursor 六维对比表、复合游标 vs Snowflake 单字段游标的取舍，见 **四、§4.2.1 与 §4.2.2**。
> 💡 **核心思想**：**任何"数据在持续插入头部"的列表，都必须用游标分页。** offset 假设了"列表是静态的"，而 Feed 恰恰是世界上最不静态的列表之一。

---

### 6. 分片策略（Sharding）

#### 6.1 三类数据，三种分片键

| 数据 | 分片键 | 分片方式 | 理由 |
|---|---|---|---|
| **Feed 缓存** `feed:{uid}` | `user_id` | 一致性哈希 / Redis slot | 读写都用 user_id 定位，**单 key 单分片**，无跨片操作 ✅ |
| **Tweet 表** `tweets` | `tweet_id` | 哈希分片（**不是范围**）⚠️ | 见 6.3 |
| **用户时间线** `user_timeline:{uid}` | `author_id` | 哈希分片 | Pull 路线按作者拉取，天然对齐 ✅ |
| **Follow 关系** | 见 6.4 | 双表双向索引 | 扇出查粉丝、读时查关注，方向相反 |

#### 6.2 Feed 缓存：为什么按 user_id 分片天然均匀

```
feed:{user_id} → CRC16(key) mod 16384 → slot → 分片

均匀性分析：
  8000 万活跃用户，25 个分片 → 每片约 320 万用户
  每个用户的 Feed 大小上限固定 800 条（≈ 50 KB）
  → 每片内存 = 320万 × 50KB = 160 GB  ✅ 完全均匀

对比：如果按【关注数】或【粉丝数】相关的键分片就会倾斜，
     因为粉丝数是幂律分布（大V 1亿 vs 普通 200）
```

⭐ **为什么这是最好的性质**：Feed 缓存的大小**由产品规则（800 条上限）封顶**，与用户的社交图规模无关。**天然抗热点**是设计出来的，不是碰巧的。

⚠️ 但仍有一个倾斜源：**读 QPS 倾斜**。某些超级活跃用户（机器人、爬虫、第三方客户端轮询）可能每秒刷 100 次 Feed。对策：按 user_id 做请求级限流（如 20 QPS/用户）。

#### 6.3 Tweet 表：Snowflake ID 的分片取舍

Snowflake ID 结构：

```
 1 bit    41 bit           10 bit      12 bit
┌─┬────────────────────┬──────────┬────────────┐
│0│  时间戳(ms)         │ 机器 ID  │  序列号     │
└─┴────────────────────┴──────────┴────────────┘
   69 年可用            1024 台     4096/ms/台

单机上限 = 4096 × 1000 = 409.6 万 ID/s
1024 台总上限 = 42 亿/s   →  峰值 5 万 QPS 绰绰有余  ✅
ID 单调递增 → 天然可按时间排序，不需要额外的时间字段
```

| 分片方式 | 优点 | 缺点 | 用在哪 |
|---|---|---|---|
| **范围分片**（按 tweet_id 区间） | 时间范围查询高效；老数据可整片归档到冷存储 ✅ | **写入永远集中在最新一片** → 单片承受全部 5 万写 QPS ❌ 经典热点 | 归档表 / 离线分析表 |
| **哈希分片** `hash(tweet_id) % N` ⭐ | 写入完全均匀（5万 / 64片 = 780 QPS/片）✅；点查（按 id 取正文）是主要访问模式，哈希完全够用 ✅ | 无法按时间范围扫 🟡 | **在线 Tweet 表** |

✅ **结论**：在线 `tweets` 表用**哈希分片**（因为 99% 的访问是 `WHERE tweet_id = ?` 的点查，由 hydrate 触发）；"按时间查某人的帖"这个需求由 **`user_timeline:{author_id}` 这张按作者分片、按时间排序的表**满足，而不是靠 tweets 表的范围扫描。

> 💡 **核心思想**：**别让一张表同时承担两种访问模式。** 点查用哈希分片的 tweets 表，时间序查用按作者分片的 user_timeline 表 —— 用一份额外的索引（存储成本翻倍，约 +$3000/月）换掉分片热点，这个交易永远值。

#### 6.4 Follow 关系表：为什么必须双向索引 ⭐

```
两张表，同一份关系，两个方向：

┌───────────────────────────────────────────────────────────┐
│ following  （我关注了谁）                                   │
│   PARTITION KEY : user_id          ← 按【关注者】分片       │
│   CLUSTERING    : followee_id                              │
│   典型行数      : 200（平均），P99 约 5000                  │
│   查询          : "u1001 关注了哪 200 个人？"               │
│   谁在用        : 【读路径】Pull 归并 + 读时过滤校验         │
└───────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────┐
│ followers  （谁关注了我）                                   │
│   PARTITION KEY : user_id          ← 按【被关注者】分片     │
│   CLUSTERING    : follower_id                              │
│   典型行数      : 200（平均），大V 1 亿  ⚠️                  │
│   查询          : "谁关注了 u2002？"（要拿去扇出）           │
│   谁在用        : 【写路径】Fan-out Worker                  │
└───────────────────────────────────────────────────────────┘
```

**为什么一张表不够？**

| 如果只有 `following` 表 | 如果只有 `followers` 表 |
|---|---|
| 扇出时要问"谁关注了作者 X" → 必须**全表扫描**所有分片 ❌ 单次扇出耗时几分钟 | 读时要问"我关注了谁" → 同样全表扫描 ❌ |
| 6000 写 QPS × 全表扫描 = 系统直接死 | 35 万读 QPS × 全表扫描 = 更快死 |

✅ **必须双写**。关注操作变成：

```python
def follow(follower_id, followee_id):
    # 两张表必须原子写入。Cassandra 用 LOGGED BATCH（跨分区，有性能损耗但保证原子性）
    batch = [
        ("INSERT INTO following (user_id, followee_id, ts) VALUES (?,?,?)",
         (follower_id, followee_id, now())),                 # 我 → 他
        ("INSERT INTO followers (user_id, follower_id, ts) VALUES (?,?,?)",
         (followee_id, follower_id, now())),                 # 他 ← 我
    ]
    cassandra.execute_batch(batch, logged=True)

    redis.sadd(f"following:{follower_id}", followee_id)       # 读路径过滤用的 SET
    redis.zadd(f"recent_follows:{follower_id}",               # §2.3 的 Pull 兜底
               {followee_id: now()})
```

| 一致性风险 | 处理 |
|---|---|
| 两张表写入不一致（一半成功） | LOGGED BATCH + 每日对账任务（Spark 扫两表 diff，修复量级 < 百万分之一）🟡 |
| 强一致要求？ | ❌ 不需要。关注关系短暂不一致的后果只是"少收到几条帖"，不是资金损失 |

#### 6.5 大 V 的粉丝列表本身就是热点 ⚠️⭐

```
头部大 V：1 亿粉丝

单个 partition 的大小：
  1 亿 × (follower_id 8B + ts 8B + 列开销 ~20B) ≈ 3.6 GB

Cassandra 官方建议：单 partition < 100 MB，< 10 万行
→ 超标 36 倍 / 1000 倍   ❌❌❌

后果：
  - 该 partition 所在节点磁盘/内存被单行撑爆
  - compaction 时要重写 3.6 GB，节点 GC / IO 打满
  - 读这一行 = 单节点扛全部流量，横向扩容完全无效（hot partition）
  - 扇出时要顺序读 1 亿行，单线程跑几十分钟  ❌
```

✅ **解法：二级分桶（Bucketing）**

```
PRIMARY KEY ((celebrity_id, bucket), follower_id)
                └── 复合 partition key ──┘

bucket = hash(follower_id) % 1000

┌──────────────────────────────────────────────────────────┐
│ (celeb_A, 0)   → 10 万粉丝 ≈ 3.6 MB   → 落在节点 17      │
│ (celeb_A, 1)   → 10 万粉丝 ≈ 3.6 MB   → 落在节点 42      │
│ (celeb_A, 2)   → 10 万粉丝 ≈ 3.6 MB   → 落在节点 5       │
│ ...                                                       │
│ (celeb_A, 999) → 10 万粉丝 ≈ 3.6 MB   → 落在节点 88      │
└──────────────────────────────────────────────────────────┘
        ↑ 1000 个桶散布到整个集群，热点被彻底打散
        ↑ 扇出时可以【1000 个 Worker 并行消费】
```

| 指标 | 不分桶 | 分 1000 桶 ⭐ |
|---|---|---|
| 单 partition 大小 | 3.6 GB ❌ | **3.6 MB** ✅ |
| 单 partition 行数 | 1 亿 ❌ | **10 万** ✅ |
| 承载节点数 | 1（+2 副本） ❌ | **分散到全集群** ✅ |
| 扇出并行度 | 1 ❌ | **1000** ✅ |
| 扇出 1 亿粉丝耗时（假设单 Worker 5 万 ZADD/s） | 1e8 / 5e4 = **2000 秒 ≈ 33 分钟** ❌ | 1000 并行 → **2 秒** ✅ |

**动态分桶策略**（避免给 200 粉丝的普通用户也建 1000 个空桶）：

| 粉丝数 | bucket 数 | 单桶行数 |
|---|---|---|
| < 1 万（99.9% 用户） | 1 | < 1 万 |
| 1 万 ~ 100 万 | 10 | ≤ 10 万 |
| 100 万 ~ 1000 万（约 1 万个大 V） | 100 | ≤ 10 万 |
| > 1000 万（头部约 100 个） | 1000 | ≤ 10 万 |

桶数记录在 `user_meta` 表，粉丝数跨越阈值时触发**在线扩桶**（双写新旧桶 → 后台迁移 → 切读 → 清旧桶）。

> 💡 **核心思想**：**任何"一行的大小取决于用户输入"的 schema 都是定时炸弹。** 幂律分布下的头部账号会把任何"一个实体一行"的设计撑爆。分桶的本质是**把 partition key 从业务实体变成"业务实体 + 人为切分维度"**，让物理分布不再受业务分布支配。
> 顺带一提 —— 大 V 的粉丝列表是热点，这也正是我们**根本不给大 V 做 Push 扇出**（改走 Pull）的第二个理由（第一个是写放大）。

---

### 7. 热点与容灾

#### 7.1 Hot Key：大 V 的 timeline 缓存

```
问题：某明星发新歌，1 亿粉丝在 10 分钟内涌入
  → 读 Feed 时人人都要 Pull 一次 user_timeline:{celeb}
  → 这个 key 的 QPS 峰值可达【50 万】
  → 但它只落在【1 个 Redis 分片】上，单实例上限约 10 万 QPS
  → 该分片 CPU 100%，不仅这个 key 挂，同分片的【其他所有 key】一起挂  ❌❌
     （连带伤害才是 hot key 最可怕的地方）
```

**三层防御，从外到内：**

```
   100 万 QPS
        │
        ▼
┌───────────────────────────────────────┐
│ ① 本地缓存（进程内 LRU，TTL 1s）        │  ← 拦掉 99.9%
│   1000 台 Feed 服务器，每台 1 次/秒回源  │
└───────────────┬───────────────────────┘
                │ 剩 1000 QPS
                ▼
┌───────────────────────────────────────┐
│ ② 多副本 key（随机后缀打散）            │  ← 再分散 10 倍
│   celeb:123:v0 ~ celeb:123:v9          │
└───────────────┬───────────────────────┘
                │ 每个副本 100 QPS
                ▼
┌───────────────────────────────────────┐
│ ③ Redis 只读副本（Replica）             │
│   READONLY + 3 个 replica 分担          │
└───────────────────────────────────────┘
```

**① 本地缓存的威力（这个数字面试一定要算给面试官听）⭐**

| 项 | 数值 |
|---|---|
| 峰值读 QPS | 100 万 |
| Feed 服务器数量 | 1000 台 |
| 单台承接 QPS | 1000 |
| 本地缓存 TTL | **1 秒** |
| 单台每秒回源次数 | **最多 1 次** |
| 打到 Redis 的 QPS | 1000 台 × 1 = **1000 QPS** |
| **降载倍数** | **1000 倍** ✅✅ |
| 代价 | 数据最多陈旧 **1 秒**（Feed 场景完全可接受） |

```python
# 进程内 LRU，只对【识别出的热 key】启用，避免污染内存
hot_local = LRUCache(maxsize=10_000, ttl=1.0)     # 1 万个热 key，TTL 1 秒

def get_celeb_timeline(celeb_id):
    key = f"celeb_tl:{celeb_id}"
    if (v := hot_local.get(key)) is not None:
        return v                                   # ① 本地命中，0 网络开销

    if is_hot(celeb_id):                           # 由 hot key 探测器实时标记
        suffix = random.randint(0, 9)              # ② 随机后缀，把流量摊到 10 个副本 key
        redis_key = f"{key}:v{suffix}"
    else:
        redis_key = key

    v = redis.zrevrange(redis_key, 0, 99)          # ③ 走 Redis 只读副本
    hot_local.set(key, v)                          # 回填本地缓存
    return v
```

**② 多副本 key 的写入代价**：写时要 `ZADD` 10 份（`v0~v9`）。大 V 发帖是低频操作（1 万大 V × 10 帖/天 = 10 万次/天 ≈ 1.2 QPS），10 倍写放大完全无感 ✅。

**Hot Key 怎么识别？**

| 方式 | 说明 |
|---|---|
| 静态规则 | 粉丝数 > 100 万的账号，永久标记为热 ✅ 覆盖 95% 场景 |
| 实时探测 | 客户端本地采样（1/100 抽样）统计 key 频次，上报到中心；超过阈值（如 1 万 QPS）广播标记 |
| Redis 自带 | `redis-cli --hotkeys`（基于 LFU，有性能开销，只适合排障不适合在线） |

#### 7.2 扇出 Worker 挂了怎么办

```
Kafka Topic: tweet.created （分区数 = 128，按 author_id 哈希）
        │
        ├──→ Fanout Worker #1  ─┐
        ├──→ Fanout Worker #2  ─┤ 消费者组，Kafka 自动 rebalance
        ├──→ ...               ─┤
        └──→ Fanout Worker #N  ─┘
                                 │
                                 ▼
                     ZADD feed:{follower} ...
```

| 故障 | 保障机制 | 后果 |
|---|---|---|
| Worker 进程崩溃 | Kafka 消费者组 rebalance，其他 Worker 接管该分区 | 秒级恢复 ✅ |
| 消息处理到一半崩了 | offset 未提交 → **at-least-once**，重启后**重新消费** | 会重复扇出 🟡 |
| 重复扇出会怎样？ | ⭐ **`ZADD` 天然幂等**：member 相同则只更新 score，不会产生重复条目 | **无害** ✅ |
| 截断也重复执行？ | `ZREMRANGEBYRANK feed:X 0 -801` 也是幂等的（已经 800 条时删 0 条） | **无害** ✅ |
| Worker 全挂 / 严重滞后 | Kafka 保留 7 天，恢复后从 offset 继续追 | Feed 延迟增大，但**不丢数据** ✅ |

> 💡 **核心思想**：**用幂等操作换掉 exactly-once。**
> Kafka 的 exactly-once 语义（事务 + 幂等 producer）性能损耗约 20~30%，且跨系统（Kafka → Redis）根本无法保证。
> 而 `ZADD` 的幂等性让 at-least-once **在语义上等价于** exactly-once —— 这是设计出来的，不是运气。
> **面试中主动说这一句，是"设计过分布式系统"和"背过分布式系统"的分水岭。**

⚠️ **注意反例**：如果扇出时顺便做"给粉丝发推送通知"，那就**不幂等**了（会重复推送）。所以通知必须走**独立的 topic + 独立的去重表**（`SETNX notify:{tweet_id}:{user_id} EX 86400`）。

**积压（Lag）监控与自愈：**

| Lag 水位 | 含义 | 动作 |
|---|---|---|
| < 1 万条 | 正常 | — |
| 1 万 ~ 10 万 | 轻微积压（可能是某大 V 刚发帖） | 告警，自动扩容 Worker |
| 10 万 ~ 100 万 | 严重积压，Feed 延迟 > 30s | 🟡 降级：跳过 7 天不活跃用户的扇出（省 60% 的写） |
| > 100 万 | 系统性故障 | ⚠️ 熔断 Push 路线，**全量改走 Pull**（读路径成本上升，但 Feed 仍可用） |

#### 7.3 优雅降级的分级预案

```
                          流量 / 故障等级
   正常 ──→ L1 轻度 ──→ L2 中度 ──→ L3 重度 ──→ L4 极端
```

| 等级 | 触发条件 | 降级动作 | 用户感知 |
|---|---|---|---|
| **正常** | — | 完整 Feed：Push 归并 + Pull 大 V + 排序模型 | 无 |
| **L1** | P99 > 200ms | 关闭个性化排序模型（省 30~50ms），改纯时间序 | 内容顺序略差 |
| **L2** | Redis 命中率 < 90% | 关闭"新关注 Pull 补齐"、over-fetch 从 1.5x 降到 1.2x；只返回 Push 那一路 | 少量内容缺失 🟡 |
| **L3** | 存储层过载 / 扇出严重积压 | **直接返回 Redis 里的旧 Feed（stale-while-revalidate）**，不做任何实时归并 | "内容没更新" 🟡 |
| **L4** | Redis 集群不可用 | 返回**全局热门 Feed**（一份全站共享的 Top 100，本地缓存 60s） | ⚠️ 不是我的 Feed，但**不白屏** |

```python
def serve_feed(user_id, cursor):
    level = degradation_controller.current_level()        # 由中心配置 + 本地熔断器共同决定
    if level >= 4:
        return global_trending_feed()                     # L4：全站热门，全内存，永不失败
    if level >= 3:
        return stale_feed_from_redis(user_id) \
               or global_trending_feed()                  # L3：旧数据优于无数据
    if level >= 2:
        return push_only_feed(user_id, cursor)            # L2：只读 ZSET，不做 Pull 归并
    return full_feed(user_id, cursor, rank=(level < 1))   # L1：关排序模型
```

> 💡 **核心思想**：**降级不是"挂了之后临时想办法"，而是提前写好的、可以一键切换的、被定期演练的代码路径。**
> 关键设计原则：**每一级降级都必须比上一级消耗更少的资源，且必须依赖更少的组件。** L4 只依赖本机内存 —— 这保证了它在任何故障下都能返回。

---

### 8. 监控指标

#### 8.1 核心指标表

| 指标 | 定义 | 正常值 | 告警阈值 | 说明这个数字变坏意味着什么 |
|---|---|---|---|---|
| **扇出延迟 P50** | 发帖 → 粉丝 Feed 可见 | < 1 s | > 3 s | Kafka 或 Worker 处理变慢 |
| **扇出延迟 P99** | 同上，尾部 | < 5 s | > 30 s | 有大 V 挤占资源，或某分片 Redis 慢 |
| **大 V 扇出完成时间** | 粉丝 > 1000 万账号的全量扇出耗时 | < 60 s | > 300 s | 分桶并行度不够，或热 partition 出现 ⚠️ |
| **Feed 读延迟 P50 / P99** | 端到端（含 hydrate） | 30ms / 150ms | P99 > 200ms | **SLO 直接指标** ⭐ |
| **Feed 缓存命中率** | ZSET 命中 / 总请求 | > 99.5% | < 95% | 内存不够被 LRU 淘汰，或有大量冷用户回归 |
| **Tweet 正文缓存命中率** | hydrate 的 MGET 命中率 | > 98% | < 90% | 冷启动或缓存被打穿 |
| **Kafka Consumer Lag** | 未消费消息数 | < 1 万 | > 10 万 | Worker 不够 / 下游 Redis 慢 |
| **Feed 重建 QPS** | 走冷路径实时归并的请求数 | < 5,000 | > 20,000 | ⚠️ 重建风暴前兆，检查 Redis 健康度 |
| **重建限流拒绝率** | 被令牌桶拒绝的比例 | 0% | > 1% | 已经在降级了，说明缓存层出问题 |

#### 8.2 资源与质量指标

| 指标 | 正常值 | 告警阈值 | 说明 |
|---|---|---|---|
| Redis 内存使用率 | < 75% | > 85% | 超过 85% 时 RDB fork 可能 OOM ⚠️ |
| Redis 单分片 QPS | < 6 万 | > 8 万 | 接近单实例上限（~10 万），准备扩容 |
| Redis 分片间 QPS 方差 | < 20% | > 50% | **出现 hot key** ⚠️ |
| 扇出写放大倍数 | 平均 200 | > 500 | 大 V 阈值设置有问题，Push/Pull 分界线要调 |
| Over-fetch 补页率 | < 0.1% | > 1% | 过滤率异常升高（大规模封号？误判？） |
| 部分结果（partial）占比 | < 0.1% | > 1% | 扇出读有分片在超时 |
| Hedged request 占比 | ~5% | > 15% | ⚠️ 下游整体变慢，对冲快要雪崩了 |
| Feed 内容为空的比例 | < 0.01% | > 0.1% | 严重问题：可能是 hydrate 全部被过滤 |

#### 8.3 业务侧指标（这些才是真正说明"Feed 好不好"的）

| 指标 | 说明 |
|---|---|
| Feed 首屏 20 条中的**有效条数** | 过滤后不足 20 条的会话比例，反映数据质量 |
| **发帖后自己首刷可见率** | 必须 = 100%（Read-your-own-writes 的直接验证）⭐ |
| **分页重复率** | 同一 tweet_id 在相邻两页出现的比例，必须 ≈ 0（验证游标分页正确性）⭐ |
| 下拉刷新后**新内容条数** | 若长期为 0，说明扇出坏了但没报警 |
| 用户会话平均翻页深度 | 用来校准"800 条"这个缓存容量是否还合理（每季度复核） |

#### 8.4 告警设计原则

```
❌ 反面教材：给上面每个指标都配一条阈值告警
   → 一次 Redis 抖动触发 20 条告警 → 告警疲劳 → 真故障被淹没

✅ 正确做法：分层
   ┌─────────────────────────────────────────────────┐
   │ Page（打电话叫醒人）：只保留【用户可感知】的       │
   │   - Feed 读 P99 > 200ms 持续 5 分钟              │
   │   - Feed 错误率 > 0.5%                           │
   │   - 发帖后自己不可见率 > 0.01%                    │
   ├─────────────────────────────────────────────────┤
   │ Ticket（工作时间处理）：容量与趋势                │
   │   - Redis 内存 > 85%、单分片 QPS > 8 万           │
   │   - Kafka lag > 10 万                            │
   ├─────────────────────────────────────────────────┤
   │ Dashboard（只看不报）：其余全部指标                │
   └─────────────────────────────────────────────────┘
```

> 💡 **核心思想**：**告警应该基于"症状（symptom）"而不是"原因（cause）"。**
> "Feed P99 > 200ms" 是症状 —— 用户真的受影响了，值得叫醒人；
> "Redis 分片 7 的 CPU 高" 是原因 —— 也许根本没影响用户（有本地缓存挡着），叫醒人就是浪费。
> Google SRE 的说法：**你的告警页面应该能直接映射到 SLO。**

---

### 面试收尾：这一章的 6 句话总结

| # | 一句话结论 | 为什么它重要 |
|---|---|---|
| 1 | **Feed 是派生数据，永远可以从 Tweet + Follow 重算** | 这是所有容灾方案的地基；但重算的成本必须被限流器显式定价 |
| 2 | **读时过滤 > 写时清理** | 取关、拉黑、删帖、封号、私密全部同构；写时清理的成本 = 副本数，读时过滤的成本恒定 |
| 3 | **Feed 只存 id 不存正文** | 把删帖/编辑/计数变化的写放大从"粉丝数"降到 1 |
| 4 | **扇出会把下游的 P99 变成上游的 P50** | 唯一解药是"允许放弃"：超时 + 部分结果 + hedged request |
| 5 | **游标分页，不用 offset；ZSET score 不能直接放 Snowflake ID** | 两个必踩的坑，说出来就是"真做过"的信号 |
| 6 | **幂等（ZADD）让 at-least-once 等价于 exactly-once** | 用数据结构的性质换掉昂贵的分布式事务 |

⭐ **如果只能留一句话给面试官**：
> "整个 Feed 系统的设计主线，是**在读路径和写路径之间搬运成本**。
> Push 把成本推给写、Pull 把成本推给读、混合方案按用户的粉丝量在两者间选择；
> 而取关/删帖/可见性这些问题，答案统一是**把成本搬到读路径**，因为读路径的成本是**每次一份且恒定**的，写路径的成本是**乘以粉丝数**的。"

---

## 九、面试实战指南

> 💡 **核心思想**：Feed 生成这道题，面试官考的不是"你知不知道 Hybrid"，而是**你能不能自己推导出 Hybrid**。答案是公开的，推导过程才是差异化信号。

---

### 1. 标准答题路径（HelloInterview Delivery Framework）

45 分钟白板面试的时间分配（以本文统一场景：DAU 2 亿、日发帖 5 亿、日读 Feed 300 亿次为例）：

| 阶段 | 时间 | 要做的事 | 关键话术 |
|------|------|---------|---------|
| **① 需求澄清**<br/>Requirements | 5 min | 划定功能范围（发帖 / 关注 / 看 Feed）；确认**非功能需求**：读写比、延迟目标、一致性要求；明确**不做**什么（评论、私信、通知、推荐算法） | "我先确认范围：核心是发帖、关注、拉取 Home Timeline。排序我先按**时间倒序**，算法排序放到最后作为扩展。<br/>非功能上我关心三点：**Feed 加载 P99 < 200ms**、**读写比约 60:1**、Feed 允许**最终一致**——晚几秒看到帖子不是事故。" |
| **② 核心实体**<br/>Core Entities | 2 min | 只列 4 个实体，别画完整 ER 图 | "四个实体：`User`、`Post`、`Follow`、`Feed`。<br/>其中 `Feed` 是**派生数据**（derived data），可以从 `Post` + `Follow` 完全重算——这点后面讲容灾时很关键。" |
| **③ API 设计**<br/>API | 5 min | 3 个接口足够；**分页参数当场就用 cursor**，不要先写 offset 再被面试官纠正 | "`POST /v1/posts`、`POST /v1/users/{id}/follow`、`GET /v1/feed?cursor=&limit=20`。<br/>分页我直接用 **cursor**（`(timestamp_ms, post_id)` 复合游标）而不是 offset，原因等会儿在深入环节展开。" |
| **④ 高层设计**<br/>High-Level Design | 10–15 min | 画出写路径和读路径两条线；**先容量估算再上架构**；按 Push → Celebrity Problem → Pull → 读放大 → Hybrid 的顺序推导 | 见下方"叙事脚本" ⭐ |
| **⑤ 深入探讨**<br/>Deep Dives | 10–15 min | 主动抛 2–3 个深水区：Celebrity 阈值与成本函数、活跃用户过滤、尾延迟放大、Feed 可重建性 | "我想深入三个点：一是**大 V 阈值怎么定**（不是拍脑袋 100 万）；二是**扇出的浪费**——90% 的粉丝根本不上线；三是 **Redis 全挂了怎么办**。" |
| **⑥ 收尾**<br/>Wrap-up | 2–3 min | 复述关键权衡 + 列出没来得及做的（监控指标、降级预案、算法排序） | "总结一下：我用 **Hybrid** 是因为写放大和读放大在这个读写比下必须两头堵；如果还有时间我想聊**监控**（扇出延迟 P99、Feed 命中率）和**算法排序的架构改造**。" |

#### ⭐ 高层设计的叙事脚本（这段是最大得分点）

**❌ 错误开场**："这题标准答案是 Hybrid，普通用户 Push、大 V Pull，我画一下。"
→ 面试官内心：这人背过题，但我不知道他懂不懂。**Senior 以上直接扣分。**

**✅ 正确开场（四步推导，每步都必须说出"为什么"）**：

```
第 1 步：先提 Push（Fan-out on Write），并讲清它为什么快
─────────────────────────────────────────────────────────
"读写比 60:1，读 QPS 峰值 100 万，写 QPS 峰值 5 万。
 既然读远多于写，我第一直觉是把代价搬到写路径 —— 发帖时就把
 post_id 推进每个粉丝的 Feed 列表里。
 这样读 Feed 就退化成一次 Redis ZSET 的 ZREVRANGEBYSCORE，
 O(log N + 20)，单次 1~2ms，100 万读 QPS 完全扛得住。"

     发帖 ──> Post Service ──> MQ ──> Fanout Worker ──┐
                                                      ├─> ZADD feed:{fan_id}
     读 Feed ──> ZREVRANGEBYSCORE feed:{uid}  (2ms)  ─┘

第 2 步：主动指出 Celebrity Problem（不要等面试官问！）
─────────────────────────────────────────────────────────
"但 Push 有个致命问题我自己先说：粉丝数是长尾分布。
 平均粉丝 200，扇出 200 次没问题；
 但头部账号粉丝 1 亿+，发一条推要写 1 亿次 ZADD。
 按单机 Redis 10 万 ops/s 算，就算打散到 100 个分片，
 也要 1 亿 / (100 × 10万) = 10 秒才写完 ——
 而且这 10 秒里整个集群的写容量被一个人吃光了（写放大打爆集群）。
 更糟的是它是**长尾延迟**：普通用户此刻发帖会被排在后面，
 P99 从 200ms 飙到几十秒。"

第 3 步：提 Pull（Fan-out on Read），说清它解决了什么
─────────────────────────────────────────────────────────
"那反过来：发帖只写自己的 Timeline（1 次写），
 读的时候现拉 N 个关注对象的 Timeline 归并。
 写放大彻底消失了，大 V 发帖 O(1)。"

第 4 步：指出 Pull 的读放大，收敛到 Hybrid
─────────────────────────────────────────────────────────
"但读放大同样致命：平均关注 200 人，一次读 Feed 要 200 次查询 + 归并。
 读峰值 100 万 QPS × 200 = 2 亿次后端查询/秒，
 而且要等最慢的那一路（尾延迟放大：单路 P99=50ms，
 200 路取 max 之后 P99 轻松破 500ms），
 P99 < 200ms 的目标直接崩掉。

 所以两个方案的成本结构是互补的：
   Push 怕『粉丝多』，Pull 怕『关注多』。
 而现实是：**只有极少数账号粉丝多，但几乎所有人关注数都不多（200）**。
 那就按账号类型分流 —— 这就是 Hybrid：
   · 普通用户（99.99%）→ Push，读路径便宜
   · 大 V（约 1 万个）→ 不扇出，读时 Pull 他们的 Timeline
   · 读 Feed = 自己的 Push Feed ∪ 关注的少量大 V Timeline，归并取 top 20"
```

> 💡 **面试话术心法**：每次抛出一个方案，**自己先攻击它**，再给出下一个方案。面试官会觉得"这人在跟我一起设计"，而不是"这人在背答案"。

---

### 2. 各级别的期望（Level Expectations）

| 级别 | 期望表现 | 典型失分点 |
|------|---------|-----------|
| **中级 (Mid-level)** | ✅ 能画出 Push 和 Pull 两张图<br/>✅ 说清各自优缺点（写放大 vs 读放大）<br/>✅ 给出基本 API：发帖、关注、拉 Feed<br/>✅ 知道 Feed 要缓存，知道用 Redis | 说不出具体数字；分页用 offset；不知道 Celebrity Problem |
| **高级 (Senior)** | 以上全部 +<br/>⭐ **主动**识别 Celebrity Problem（不用面试官提示）<br/>⭐ 提出 Hybrid 并说清分流规则<br/>⭐ Feed 只存 `post_id`（+ score），不存正文，并能算出存储差异（4TB vs 80TB）<br/>⭐ **cursor 分页**及其正确性论证<br/>⭐ 发帖接口只写 DB + 投 MQ，**异步扇出**，接口 P99 < 50ms<br/>⭐ 会做容量估算：扇出写 QPS ≈ 5 亿 × 200 / 86400 ≈ **115 万/s** | 只说"用消息队列异步处理"但说不出扇出量级；Hybrid 讲了但不解释谁是大 V |
| **Staff / Staff+** | 以上全部 +<br/>⭐ **动态阈值 / 成本函数**：不是写死 100 万，而是 `min(fanout_cost, pull_cost)`，带滞回（hysteresis）防抖<br/>⭐ **活跃用户过滤**：只扇出给 30 天内活跃的粉丝，写量从 115 万/s 降到 ~58 万/s（省 50%）<br/>⭐ **尾延迟放大**：N 路并发取 max 的数学后果，用 hedged request / 超时降级<br/>⭐ **读时过滤原则**：拉黑、删帖、隐私变更**不回写** 2 亿个 Feed，读时过滤<br/>⭐ **Feed 可重建性**：Feed 是 derived data，Redis 全丢可从 Post + Follow 重算，设计重建限流<br/>⭐ **容灾降级链**：Feed 缓存 miss → 降级为纯 Pull → 再降级为"只看大 V + 热门"<br/>⭐ **监控指标**：扇出端到端延迟 P99、Feed 命中率、每帖扇出条数分布、MQ Lag<br/>⭐ 主动讨论**成本**：Redis 内存 TB 级的钱怎么省 | 只做技术方案不谈成本/运维；不提可观测性；把 Feed 当强一致数据设计 |

---

### 3. 高频追问及标准回答（FAQ）

#### Q1: Push 和 Pull 怎么选？

**A**: 看**成本结构的方向**，不看喜好。
Push 的成本 ∝ **粉丝数**（写放大），Pull 的成本 ∝ **关注数 × 读频次**（读放大）。
本场景读写比 60:1，写 6000 QPS / 读 35 万 QPS——默认应该把成本压到写侧，即 **Push 打底**。
但粉丝数是长尾分布（平均 200，头部 1 亿），所以对**尾部的极端值**（大 V）单独用 Pull 兜底。
一句话：**"用 Push 服务 99.99% 的账号，用 Pull 处理那 1 万个异常值。"**

#### Q2: 大 V 发一条推，1 亿粉丝怎么办？

**A**: **不扇出**。大 V 的帖子只写自己的 User Timeline（1 次写，O(1)），
读 Feed 时由读侧去拉：`ZREVRANGEBYSCORE timeline:{celeb_id} ... LIMIT 0 20`。
关键收益是**共享**：这条 Timeline 的缓存被 1 亿个读请求共用，缓存命中率接近 100%，
而扇出方案要写 1 亿份**几乎不会被读到**的副本。
数量级对比：扇出 1 亿次 ZADD ≈ 集群阻塞 10 秒；Pull 侧只是每个读请求多 1 次并发查询（+3~5ms）。
补一句止损：大 V Timeline 用**本地缓存 + Redis 两级**，热点 key 再做多副本打散（`timeline:{id}:{replica_0..9}`）防单分片打爆。

#### Q3: Feed 里存推文正文还是 id？为什么？

**A**: **只存 id**（`post_id` + score，可选 `author_id` 用于读时过滤），正文读时 hydrate。
三个理由，按重要性排序：

| 维度 | 只存 id | 存正文 |
|------|--------|--------|
| 存储 | 每条约 64B（ZSET 实际开销）→ 2 亿用户 × 800 条 ≈ **10 TB** | 每条约 400B → ≈ **40 TB**（6 倍，多花几百万美元/年） |
| 一致性 | 帖子被编辑/删除，**只改一处**（Post 表 + Post 缓存） | 要改 N 份副本，或者永远显示脏数据 ❌ |
| 读时过滤 | 拿到 id 后可以统一做拉黑/删帖/隐私过滤 | 正文已经在 Feed 里，过滤逻辑散落 |

代价是读路径多一次批量查询：`MGET post:{id1}...post:{id20}`，一次 RTT ≈ 5~10ms，
在 200ms 预算里完全可接受。**这是典型的"用一点读延迟换 6 倍存储 + 一致性"**。

#### Q4: 为什么用 cursor 分页不用 offset？

**A**: 因为 Feed 是**高速变化的流**，offset 在流上不成立。
用户看完第 1 页（`OFFSET 0 LIMIT 20`）后，这 2 秒内关注的人又发了 5 条新帖，
再请求 `OFFSET 20` 时，原来的第 16~20 条被挤到了 21~25 —— 用户会**重复看到 5 条**（漏读同理）。
cursor 用**内容位置**而非**序号**：`cursor = base64(last_score, last_post_id)`。

```bash
# 首屏：取最新 20 条
ZREVRANGEBYSCORE feed:{uid} +inf -inf LIMIT 0 20
# 下一页：从上次最后一条的 score 严格往前取（"(" 表示开区间，排除等于）
ZREVRANGEBYSCORE feed:{uid} (1723699200000 -inf LIMIT 0 20
```

附带好处：**性能是 O(log N + 20) 恒定**，而 SQL 的 `OFFSET 10000` 要扫描并丢弃 10000 行。
⚠️ 同毫秒撞车时 score 相同会漏，所以游标要带 `post_id` 做 tie-breaker（⚠️ 同毫秒撞车时 score 相同会漏，所以游标必须带 `post_id` 做 tie-breaker：score 只能放毫秒时间戳（< 2^53，见 §1.5），同 score 时靠**定长零填充的 member 字典序**兜底，客户端对边界上同 score 的元素再做一次去重。**不能把时间戳左移后拼序列号当 score**（`timestamp_ms << 20 | seq` ≈ 2^60.7，远超 double 的精确整数上限 2^53 = 9.007×10^15，低位会被整段抹平——这正是 §1.5 里 Snowflake 直接当 score 的同一个坑）。）。

#### Q5: 用户关注了新的人，历史帖子要补进 Feed 吗？

**A**: 要，但**异步 + 有限补**。
关注动作返回后，投一条 `follow_created` 事件到 MQ，Backfill Worker 拉取被关注者最近 **N=50 条 / 7 天内**的帖子，`ZADD` 进关注者的 Feed，再 `ZREMRANGEBYRANK` 裁回 800 条上限。
不补全量的三个原因：① 用户不会往下翻 1000 条；② 全量补会把 Feed 打满、挤掉其他人；③ 大 V 被关注是高频事件（1 万大 V × 每天海量新粉），全量补会打爆写侧。
⚠️ 如果被关注的是**大 V**，则**什么都不用做**——大 V 本来就走 Pull，下次读 Feed 自然合并进来，这是 Hybrid 的白送红利。
兜底：补写完成前（通常 < 2s），读路径的 Pull 分支已经能看到大 V 内容，用户感知很弱。

#### Q6: 取关了怎么把他的帖子从 Feed 里删掉？

**A**: **不删，读时过滤**（Filter on Read）。
理由：Feed 里可能散落着他的几十条帖子，要精确删除得先知道有哪些 id，等于一次范围扫描 + 多次 `ZREM`；而取关是低频操作但**正确性要求不高**（多看一眼旧帖不是事故）。
做法：
1. 读路径拿到 20 个 `(post_id, author_id)` 后，与用户的**关注集合 / 拉黑集合**做一次内存过滤（关注列表 200 人，本地缓存，过滤耗时 < 1ms）；
2. 过滤掉后不足 20 条就**多取一批**（over-fetch：一次取 30~40 条再截断）；
3. 惰性清理：下次该 Feed 被裁剪（trim）时自然淘汰。

> 💡 **读时过滤原则**：拉黑、取关、删帖、账号封禁、隐私变更——**一律读时过滤，绝不回写 N 份 Feed**。写侧只负责"大概率正确"，读侧负责"最终正确"。

#### Q7: Redis 里的 Feed 数据丢了怎么办？

**A**: 不慌，**Feed 是派生数据（derived data），可以完全重算**：
`Feed(u) = merge(Post(v) for v in Follow(u))`，Source of Truth 是 Post 表和 Follow 表（都在持久化存储里）。
三层预案：

| 层级 | 手段 | 效果 |
|------|------|------|
| 🟡 单分片丢失 | 该分片用户降级为**纯 Pull**（读时归并 200 个关注对象，P99 涨到 ~400ms 但可用） | 局部降级，无数据丢失 |
| 🟡 大面积丢失 | 后台 Rebuild Worker **按活跃度排序**重建（先重建今天登录过的用户），**限流** 1 万 用户/s（与 §1.6 防线② 同一个令牌桶 → 后端 200 万 QPS，在 500 万容量内 ✅），当前在线的约 500 万用户 ≈ 8 分钟恢复，2 亿 DAU 全量约 5.6 小时 | 避免重建风暴打爆 DB |
| ⚠️ 极端情况 | 前端降级："只显示大 V + 热门内容"（这部分是共享缓存，不依赖个人 Feed） | 保证首屏不白屏 |

顺带说一句设计准则：**正因为 Feed 可重建，我们才敢把它放在纯内存的 Redis 里、才敢不开 AOF everysec**——这是有意识的取舍，不是疏忽。

#### Q8: 怎么保证用户能立刻看到自己刚发的帖子？

**A**: 这是典型的 **Read-Your-Own-Writes** 问题。异步扇出意味着自己的帖子可能 1~2 秒后才进 Feed，但用户对**自己的**帖子零容忍。
三个手段（面试里说第 1 个就够，说到第 3 个是加分）：
1. **同步自写**：发帖时在**主流程内**先 `ZADD feed:{self_id}`（1 次写，< 1ms），再投 MQ 扇出给别人。自己立刻可见。
2. **客户端乐观插入**：前端本地先把这条塞进列表顶部（灰色 "发送中" 态），服务端确认后转正。这是体感最好的方案。
3. **读路径兜底**：读 Feed 时永远额外 `merge` 自己 User Timeline 的最近 5 条（成本 1 次查询），保证任何情况下自见性成立。

#### Q9: 扇出 Worker 重复消费会不会导致 Feed 里出现重复帖子？

**A**: **不会**，这是选 Redis **ZSET 而不是 List** 的一个隐藏理由。
ZSET 的 member 唯一：`ZADD feed:{uid} 1723699200000 "987654321"` 执行两次，第二次只是**更新 score**，集合大小不变——天然幂等（idempotent）。
所以扇出链路可以放心用 **At-Least-Once** 语义的 MQ（Kafka），不需要昂贵的 Exactly-Once。
⚠️ 但要注意两个坑：
- score 必须**幂等地取自帖子本身**（`post.created_at_ms`），不能用 `now()`——否则重复消费会把老帖顶到 Feed 顶部；
- 用 List / `LPUSH` 就没有这个性质，会真的插两条，还得额外做去重——这是 List 方案的隐性成本。

#### Q10: 阈值定 100 万粉丝，那 99 万粉丝的用户怎么办？

**A**: 说明"100 万"这个硬阈值本身就是错的答案，正确的是**成本函数 + 滞回 + 灰度带**。

```python
# 判定是否扇出：比较两条路径的真实成本，而不是拍一个固定数字
def should_fanout(author):
    # 写成本：只算 30 天内活跃的粉丝（不活跃的推了也没人看）
    write_cost = author.active_follower_count * W_WRITE      # W_WRITE ≈ 1 单位
    # 读成本：这个作者的内容每天被读多少次，每次归并要多付一路查询
    read_cost  = author.daily_feed_read_count * W_MERGE      # W_MERGE ≈ 3~5 单位
    return write_cost < read_cost                            # 写更便宜就扇出

# 滞回（hysteresis）：防止刚好卡在阈值上的账号来回横跳，导致 Feed 忽有忽无
UPPER, LOWER = 1_200_000, 800_000
def update_mode(author):
    if author.mode == "PUSH" and author.active_follower_count > UPPER:
        author.mode = "PULL"      # 涨过上界才切 Pull
    elif author.mode == "PULL" and author.active_follower_count < LOWER:
        author.mode = "PUSH"      # 跌破下界才切回 Push
    # 80万~120万之间：保持原状，不动
```

**灰度带（Hybrid of Hybrid）**：80 万~120 万这一段可以两头都做一点——只扇出给**互动最活跃的 top 20 万粉丝**（他们最可能马上刷到），剩下的走 Pull。
再补一句工程实践：模式切换要**双写过渡**（切换后的 5 分钟内 Push 和 Pull 都生效，读侧靠 ZSET 去重），避免切换瞬间出现内容空洞。

#### Q11: 关注了 5000 个人的用户怎么优化？

**A**: 这是 Q10 的**镜像问题**——Push 路径不怕他（他只是别人的粉丝之一），但 **Pull 路径怕他**：如果这 5000 人里有 50 个大 V，一次读 Feed 就要并发 50 路查询，尾延迟放大让 P99 直接爆掉（50 路各自 P99 = 30ms，取 max 后 ≈ 100ms+）。
四个手段：
1. **强制全 Push**：对这类重度关注用户，把他所有关注对象（含大 V）都改成扇出给他。代价是每个大 V 多写几万条，但这类用户总数极少（< 0.1%），总成本可忽略。💡 这是最优解，也是 Hybrid 分流规则的**第二个维度**。
2. **限制并发路数**：只 Pull 最近 7 天有互动 / 最常点开的 **top 20 个**大 V，其余降级为"下拉时再补"。
3. **Celebrity Bundle 二级缓存**：把"最热的 1000 个大 V 的最新帖子"预先归并成一个共享的热榜 ZSET，5 秒更新一次，读时用它替代 N 路并发。
4. **超时降级 / hedged request**：给每一路 15ms 超时，超时就丢弃该路（Feed 少一条没人发现），保住 P99。
产品侧兜底：直接**限制关注上限**（如 5000），这也是 Twitter/微博的真实做法——**产品约束是系统设计的合法工具**。

#### Q12: 如果要加算法排序（不是纯时间序），架构要怎么变？

**A**: 从"一步取 20 条"变成经典的**两阶段：Retrieval（召回）→ Ranking（排序）**。

```
【时间序架构】
 Feed ZSET(score=timestamp) ──取 20──> hydrate ──> 返回

【算法排序架构】
 ① 召回 Retrieval：Feed ZSET + 大V Timeline + 兴趣召回/热门召回
        └─> 候选池 500~1000 条（这里 score 仍可用时间，只保证"新"）
 ② 特征 Feature Store：用户画像 / 作者质量 / 帖子统计（低延迟 KV，P99 < 5ms）
 ③ 打分 Ranking Service：批量推理 500 候选，预测 p(click)/p(like)/p(dwell)
        └─> final_score = Σ wᵢ · pᵢ  （多目标加权）
 ④ 重排 Re-rank：多样性打散（同作者最多 2 条）、去重、广告插入、合规过滤
 ⑤ 截断 top 20 ──> hydrate ──> 返回
```

架构上的四个具体变化：

| 变化点 | 说明 |
|--------|------|
| **ZSET score 语义变了** | 排序分依赖"当前用户 × 当前时刻"，**不能预存**在 Feed 里（否则 2 亿人 × 每次更新都要重写）。所以 Feed 退化为**候选池**，排序放到读路径在线做 |
| **延迟预算重排** | Ranking 要吃掉 30~50ms（特征拉取 5ms + 批量推理 20~30ms）。200ms 预算下必须压缩其他环节：候选池限 500 条、特征全内存、模型走 GPU batch |
| **多了离线/近线链路** | 特征计算、模型训练、Embedding 更新走 Flink/Spark 近线管道；在线只做推理 |
| **必须有 A/B 和回退** | 排序会直接影响留存，需要实验框架 + 一键回退到时间序（作为**降级兜底**，模型服务挂了就退回 ZSET 时间序） |

> 💡 一句话说给面试官听：**"引入 Ranking 之后，Feed Store 从『答案』变成了『候选集』——Push/Pull 的取舍完全不变，变的是候选集之后多挂了一条排序链路。"**

---

### 4. 常见错误（面试中的扣分项）

| # | 错误 | 为什么扣分 | 正确做法 |
|---|------|-----------|---------|
| 1 | ❌ 在 Feed 缓存里存**推文正文** | 存储从 4TB 膨胀到 80TB；帖子编辑/删除时 N 份副本全是脏数据 | 只存 `post_id` + score，读时批量 hydrate |
| 2 | ❌ **同步扇出**（发帖接口里 `for fan in followers: write()`） | 200 个粉丝还行，1 万个粉丝接口就超时；发帖 P99 被粉丝数绑架，且 DB 抖动直接让发帖失败 | 发帖只做"写 Post + 投 MQ"（P99 < 50ms），扇出交给 Worker 异步做 |
| 3 | ❌ 用 **offset 分页** | Feed 是持续变化的流，offset 必然导致重复/漏读；`OFFSET 10000` 还要扫描丢弃 1 万行 | cursor 分页：`(timestamp_ms, post_id)` 复合游标 + 开区间查询 |
| 4 | ❌ 只说 "用 Hybrid"，**不解释怎么判定谁是大 V** | 这是整道题唯一的核心决策点，跳过它等于没答 | 给出成本函数 + 滞回双阈值 + 灰度带（见 Q10） |
| 5 | ❌ 忽略**不活跃用户**的浪费 | 2 亿 DAU 背后可能有 15 亿注册用户，给僵尸粉扇出白烧 70%+ 的写容量和内存 | 只扇出给 30 天内活跃的粉丝；非活跃用户回归时按需 Pull 重建 |
| 6 | ❌ 忘了 **Follow 关系需要正反两张表** | 扇出要查 "谁关注了我"（followers），读时过滤要查 "我关注了谁"（followees）。单向表必然有一边是全表扫描 | 双向索引：`followers:{uid}` 和 `followees:{uid}` 各存一份（或 `(follower, followee)` + `(followee, follower)` 两套主键） |
| 7 | ❌ **没有算容量就直接上架构** | 不算就不知道 115 万次/s 的扇出写、4TB（裸）/~10TB（含 ZSET 开销）内存、100+ 个 Redis 分片——架构决策就没有依据，纯靠背 | 先花 2 分钟：读 QPS 35万/峰值 100万、写 QPS 6000/峰值 5万、扇出 115万/s、存储 4TB（裸） |
| 8 | ❌ 把 Feed 当成**必须强一致**的数据 | 为了强一致会引入分布式事务/同步扇出，性能崩且完全没必要 | 明确声明：Feed 是**最终一致**，SLA 是"P99 5 秒内可见"；只有"自己发的帖子"要求立即可见（见 Q8） |
| 9 | ❌ 扇出用 `now()` 当 score | 重复消费会把老帖顶上 Feed 顶部，用户看到乱序 | score 恒取 `post.created_at_ms`，保证幂等 |
| 10 | ❌ 只谈架构不谈**降级和监控** | Staff 级必考：系统坏掉的时候会发生什么 | 降级链（Q7）+ 四个核心指标：扇出端到端 P99、Feed 命中率、MQ Lag、每帖扇出条数分布 |

---

### 5. 一句话总结（Cheat Sheet）

```
【决策树】
粉丝数上限可控（如朋友圈好友上限 5000）   → 纯 Push
    · 写放大有硬上界，读路径永远 O(1)，简单就是美

关注数少 / 写多读少 / 大 V 遍地           → 偏 Pull
    · 典型：企业 IM 频道、订阅数极少的场景

通用社交产品（Twitter/微博/IG）          → Hybrid  ⭐
  ├─ 普通用户  → Push 到【活跃】粉丝的 Redis ZSET（score = created_at_ms）
  ├─ 大 V      → 不扇出，读时 Pull 共享 Timeline 缓存（1 亿人共用一份）
  ├─ 重度关注者(关注 5000+) → 反向特判：强制全 Push，避免 N 路归并
  └─ 读路径    → 两路归并 + 去重 + 排序 + 读时过滤 + hydrate

【关键数字（本文统一场景）】
  DAU 2亿 | 平均关注/粉丝 200 | 大V ≈ 1万个(粉丝>100万)
  写:  5亿帖/天 → 平均 6,000 QPS，峰值 5万 QPS
  读: 300亿次/天 → 平均 35万 QPS，峰值 100万 QPS  （读写比 60:1）
  扇出: 5亿 × 200 = 1000亿次/天 ≈ 115万 次/s（活跃过滤后 ≈ 30万/s）
  存储:   存储: 2亿 × 800条 × 24B ≈ 4 TB 裸数据（含 ZSET 开销 ≈ 10 TB；活跃过滤后 ≈ 1.1 TB）（存正文则 40 TB）
  目标: Feed P99 < 200ms  |  首屏 20 条  |  Feed 最终一致，5s 内可见

【读路径延迟预算（P99 < 200ms 怎么凑出来的）】
  网关+鉴权 10ms → Feed ZSET 2ms → 大V Timeline 并发 5ms
  → 归并去重 1ms → 读时过滤 3ms → 批量 hydrate 15ms
  → 序列化+网络 30ms  ≈ 66ms（留 3 倍余量给重试和抖动）

【三条铁律】
  1. Feed 只存 id，正文读时 hydrate      —— 省 6 倍存储 + 天然一致
  2. 一切"取消类"操作读时过滤，不回写    —— 拉黑/取关/删帖/隐私
  3. Feed 是 derived data，可重建         —— 所以敢放纯内存、敢降级
```

---

### 6. 关联知识点

这道题是"系统设计知识的枢纽题"，几乎每个深入方向都会牵出另一个独立考点：

| 关联知识点 | 在本题中的落点 | 笔记链接 |
|-----------|--------------|---------|
| **一致性哈希** | Feed ZSET 按 `user_id` 分片到 100+ 个 Redis 节点；扩容时如何避免全量搬迁 | [一致性哈希](../一致性哈希/一致性哈希.md) |
| **Sharding 分片** | Post 表按 `post_id` 分片、Follow 表按 `user_id` 分片；大 V 热点分片的打散策略 | [Sharding 分片](../Sharding分片/Sharding.md) |
| **CAP 定理** | Feed 明确选择 **AP**：可用性优先、最终一致（"晚 5 秒看到帖子"不是事故） | [CAP 定理](../../core/BE/CAP定理/CAP定理.md) |
| **数据库索引** | `follows(followee_id, created_at)` 复合索引支撑扇出取粉丝；`posts(author_id, created_at DESC)` 支撑 Pull 取 Timeline；cursor 分页依赖索引有序性 | [数据库索引](../../core/BE/DataBase_Index/index.md) |
| **缓存策略** | 三层缓存（本地 → Redis Feed → Post 内容缓存）；Cache-Aside vs Write-Through；热点 key 打散；缓存击穿/雪崩在大 V Timeline 上的表现 | *（本仓库暂无独立笔记）* |
| **消息队列** | Kafka 承接扇出任务：分区键选 `author_id`（保证同作者有序）、At-Least-Once + 幂等 ZADD、消费者组扩缩容、Lag 监控与积压回压 | *（本仓库暂无独立笔记）* |
| **限流与降级** | 扇出 Worker 限流防打爆 Redis；Feed 重建限流 5 万用户/s；读路径超时降级 | *（本仓库暂无独立笔记）* |

---

## 十、参考资料

> ⚠️ 以下为公开可查的真实资料；部分条目仅给出标题与出处，请自行按标题检索最新链接。

1. **HelloInterview — System Design Problem Breakdowns: Design Facebook's News Feed**
   面试导向的完整拆解（Feed 的扇出策略与读放大问题），本文的答题框架（Requirements → Core Entities → API → High-Level Design → Deep Dives）即出自其 Delivery Framework。
   https://www.hellointerview.com/learn/system-design/problem-breakdowns/fb-news-feed

2. **Raffi Krikorian — "Real-Time Delivery Architecture at Twitter"（QCon NYC 2012，InfoQ 演讲）**
   Twitter Timeline 扇出架构的经典一手资料：Fan-out on Write 的实现、Redis 中每个用户 800 条 Timeline 的设计、大 V 的特殊处理。
   https://www.infoq.com/presentations/Real-Time-Delivery-Twitter

3. **Raffi Krikorian — "Timelines at Scale"（QCon SF 2012，InfoQ 演讲）**
   上一条的姊妹演讲，重点讲 Timeline 的存储与合并、扇出延迟的 P99 控制。
   （注意勿与 Arya Asemanfar 的 "Timelines @ Twitter" 混淆，那是另一场演讲。）
   https://www.infoq.com/presentations/Timelines-Twitter/

4. **Twitter 开源推荐系统 `twitter/the-algorithm`**
   2023 年开源的 Home Mixer / 召回与排序链路源码，是"时间序 → 算法排序"架构改造（Q12）最权威的实证材料。
   https://github.com/twitter/the-algorithm

5. **Redis 官方文档 — Sorted Sets（ZSET）数据类型与命令**
   `ZADD` / `ZREVRANGEBYSCORE` / `ZREMRANGEBYRANK` 的语义与复杂度（本文 cursor 分页与幂等扇出的基础）。
   https://redis.io/docs/latest/develop/data-types/sorted-sets/

6. **Redis 官方文档 — "Redis patterns example"（即 Retwis：Design and implementation of a simple Twitter clone）**
   Redis 官方的 Twitter 克隆教程：Timeline 用 List（LPUSH + LTRIM 做定长）、关注/粉丝关系用 ZSET，最小可运行的写扩散范例。
   ⚠️ 注意它用的是 List 而非本文推荐的 ZSET —— 正好可以对照本文 §五「List vs ZSET」那张表理解取舍。
   https://redis.io/docs/latest/develop/clients/patterns/twitter-clone/

7. **Alex Xu《System Design Interview – An Insider's Guide, Vol.1》**
   - Chapter 11: **Design a News Feed System**（Feed 发布与构建的标准流程图）
   - Chapter 5: **Consistent Hashing** / Chapter 7: **Design A Unique ID Generator in Distributed Systems**（分别对应本文 Feed ZSET 分片扩容与 score 单调唯一的设计）
   - Vol.2 无 Feed／社交图谱／Feed 排序专章；若需 ZSET 排行榜的类比实现，可参考 Vol.2 Chapter 10: **Real-time Gaming Leaderboard**

8. **Instagram Engineering**
   - "Sharding & IDs at Instagram"（2011，ID 生成与分片，对应本文 score 单调唯一的设计）
     https://instagram-engineering.com/sharding-ids-at-instagram-1cf5a71e5a5c
   - Lisa Guo — "Scaling Instagram Infrastructure"（QCon London 2017，InfoQ 演讲，讲缓存分层与读路径优化）
     https://www.infoq.com/presentations/instagram-scale-infrastructure

9. **Meta Engineering — Feed Ranking 公开资料**
   - "How machine learning powers Facebook's News Feed ranking algorithm"（engineering.fb.com）
   - Meta 透明度中心《Our approach to Facebook Feed ranking》（多目标加权与召回-排序两阶段的官方说明）
   出处：engineering.fb.com / transparency.meta.com

10. **Nishtala et al. — "Scaling Memcache at Facebook"（USENIX NSDI 2013）**
    大规模读多写少系统的缓存分层、热点 key、缓存击穿与失效风暴的经典论文，对应本文的 hydrate 与多级缓存设计。
    出处：USENIX NSDI 2013 Proceedings

11. **Apache Kafka 官方文档 — Consumer Groups / Delivery Semantics**
    扇出链路选择 At-Least-Once + 幂等写入（而非昂贵的 Exactly-Once）的依据。
    https://kafka.apache.org/documentation/
