# 数据库索引知识点总结

## 目录

1. [索引基础概念](#索引基础概念)
2. [索引的数据结构](#索引的数据结构)
3. [索引的类型](#索引的类型)
4. [聚簇索引与非聚簇索引](#聚簇索引与非聚簇索引)
5. [索引设计原则](#索引设计原则)
6. [索引失效场景](#索引失效场景)
7. [索引优化策略](#索引优化策略)
8. [实际应用场景](#实际应用场景)

---

## 索引基础概念

### 什么是索引？

索引是一种**数据结构**，用于帮助数据库高效地查询和检索数据。就像书的目录一样，索引可以快速定位到数据的存储位置，而不需要扫描整个表。

### 为什么需要索引？

- **提高查询速度**：从 O(n) 的全表扫描优化到 O(log n) 的查找
- **加速排序和分组**：利用索引的有序性
- **保证数据唯一性**：通过唯一索引
- **加速表连接**：在 JOIN 操作中提高效率

### 索引的代价

- **存储空间**：索引需要额外的磁盘空间
- **写入性能下降**：INSERT、UPDATE、DELETE 操作需要维护索引
- **维护成本**：需要定期优化和重建索引

---

## 索引的数据结构

### 1. B-Tree（平衡树）

- **结构特点**：多路平衡查找树，每个节点可以有多个子节点
- **优点**：
  - 所有叶子节点在同一层，查找效率稳定
  - 支持范围查询和精确查询
  - 适合磁盘存储（减少 I/O 次数）
- **缺点**：
  - 数据同时存储在叶子节点和非叶子节点中
  - 范围查询效率相对 B+Tree 较低

### 2. B+Tree（B-Tree 的变种）⭐

**MySQL InnoDB 默认使用的索引结构**

- **结构特点**：
  - 所有数据都存储在叶子节点
  - 非叶子节点只存储键值和指针
  - 叶子节点之间通过指针连接，形成有序链表
- **优点**：

  - 范围查询效率高（叶子节点链表）
  - 非叶子节点不存数据，可以存储更多键值，树的高度更低
  - 磁盘 I/O 次数更少
  - 所有查询都要到叶子节点，性能稳定

- **B+Tree vs B-Tree**：

  ```
  B-Tree:
          [10, 20]
         /   |    \
      [5]  [15]  [25,30]

  B+Tree:
          [10, 20]
         /   |    \
      [5]→[10,15]→[20,25,30]
  (数据只在叶子节点，叶子节点相连)
  ```

### 3. Hash 索引

- **结构特点**：基于哈希表实现
- **优点**：
  - 等值查询速度极快 O(1)
  - 简单高效
- **缺点**：
  - 不支持范围查询
  - 不支持排序
  - 不支持模糊查询
  - 存在哈希冲突
  - 只有 Memory 引擎支持显式 Hash 索引

### 4. Full-Text（全文索引）

- **适用场景**：文本内容的全文检索
- **支持引擎**：MyISAM、InnoDB（5.6+）
- **应用**：文章搜索、关键词匹配

### 5. R-Tree（空间索引）

- **适用场景**：地理位置、空间数据
- **应用**：GIS 系统、地图应用

---

## 索引的类型

### 1. 普通索引（INDEX）

```sql
CREATE INDEX idx_name ON users(name);
```

- 最基本的索引类型
- 没有任何限制

### 2. 唯一索引（UNIQUE）

```sql
CREATE UNIQUE INDEX idx_email ON users(email);
```

- 索引列的值必须唯一
- 允许有空值（NULL）

### 3. 主键索引（PRIMARY KEY）

```sql
ALTER TABLE users ADD PRIMARY KEY (id);
```

- 特殊的唯一索引
- 不允许空值
- 一张表只能有一个主键

### 4. 复合索引（联合索引）

```sql
CREATE INDEX idx_name_age ON users(name, age);
```

- 由多个列组合而成
- **遵循最左前缀原则**

### 5. 全文索引（FULLTEXT）

```sql
CREATE FULLTEXT INDEX idx_content ON articles(content);
SELECT * FROM articles WHERE MATCH(content) AGAINST('关键词');
```

### 6. 前缀索引

```sql
CREATE INDEX idx_email_prefix ON users(email(10));
```

- 只对字段的前 n 个字符建立索引
- 节省空间，但可能降低查询精度

---

## 聚簇索引与非聚簇索引

### 聚簇索引（Clustered Index）

- **定义**：数据行的物理顺序与索引顺序一致
- **特点**：
  - 叶子节点存储的是**完整的数据行**
  - 一张表只能有一个聚簇索引
  - InnoDB 中主键就是聚簇索引
- **优点**：
  - 范围查询速度快
  - 直接获取数据，不需要回表
- **缺点**：
  - 插入/更新可能导致页分裂
  - 二级索引需要回表

### 非聚簇索引（Secondary Index/二级索引）

- **定义**：索引顺序与数据物理顺序无关
- **特点**：
  - 叶子节点存储的是**主键值**
  - 一张表可以有多个非聚簇索引
- **查询过程**：
  1. 先在非聚簇索引中找到主键值
  2. 再根据主键值在聚簇索引中查找数据（**回表**）

### 回表（Table Lookup）

```sql
-- 假设 name 是非聚簇索引
SELECT * FROM users WHERE name = 'Alice';

-- 查询过程：
-- 1. 在 name 索引中找到 'Alice' 对应的主键 id=1
-- 2. 根据 id=1 在聚簇索引中查找完整数据（回表）
```

### 覆盖索引（Covering Index）⭐

**避免回表的优化方式**

```sql
-- name 和 age 建立联合索引
CREATE INDEX idx_name_age ON users(name, age);

-- 只查询索引列，不需要回表
SELECT name, age FROM users WHERE name = 'Alice';
```

---

## 索引设计原则

### 1. 选择合适的列建立索引

✅ **适合建索引的列**：

- WHERE 子句中频繁查询的列
- JOIN 关联的列
- ORDER BY / GROUP BY 的列
- 区分度高的列（选择性高）

❌ **不适合建索引的列**：

- 数据变动频繁的列
- 区分度低的列（如性别、状态等）
- 很少使用的列
- 数据类型过大的列（TEXT, BLOB）

### 2. 选择性（Selectivity）

```sql
-- 计算列的选择性
SELECT COUNT(DISTINCT column) / COUNT(*) FROM table;

-- 选择性越接近 1 越适合建索引
-- 如：身份证号选择性接近 1，性别选择性约 0.5
```

### 3. 最左前缀原则（联合索引）⭐

```sql
-- 创建联合索引
CREATE INDEX idx_a_b_c ON table(a, b, c);

-- ✅ 可以使用索引
WHERE a = 1
WHERE a = 1 AND b = 2
WHERE a = 1 AND b = 2 AND c = 3
WHERE a = 1 AND c = 3  -- 只用到 a

-- ❌ 无法使用索引
WHERE b = 2
WHERE c = 3
WHERE b = 2 AND c = 3
```

### 4. 索引列不要参与计算

```sql
-- ❌ 索引失效
SELECT * FROM users WHERE age + 1 = 20;

-- ✅ 可以使用索引
SELECT * FROM users WHERE age = 19;
```

### 5. 合理使用前缀索引

```sql
-- 对于长字符串，使用前缀索引
CREATE INDEX idx_email ON users(email(10));

-- 权衡：索引大小 vs 查询精度
```

### 6. 控制索引数量

- 索引不是越多越好
- 每个索引都会增加写操作的开销
- 一般建议单表索引不超过 5-6 个

---

## 索引失效场景

### 1. 使用 != 或 <> 操作符

```sql
-- ❌ 索引失效
SELECT * FROM users WHERE age != 20;
```

### 2. 使用 IS NULL 或 IS NOT NULL

```sql
-- ❌ 可能失效（取决于优化器）
SELECT * FROM users WHERE age IS NULL;
```

### 3. 使用 OR 连接

```sql
-- ❌ 如果 OR 两边有一个列没有索引，整体失效
SELECT * FROM users WHERE name = 'Alice' OR email = 'alice@example.com';
```

### 4. 模糊查询以 % 开头

```sql
-- ❌ 索引失效
SELECT * FROM users WHERE name LIKE '%Alice';

-- ✅ 可以使用索引
SELECT * FROM users WHERE name LIKE 'Alice%';
```

### 5. 类型转换

```sql
-- ❌ phone 是 VARCHAR，使用数字查询会发生隐式转换
SELECT * FROM users WHERE phone = 12345678901;

-- ✅ 正确使用
SELECT * FROM users WHERE phone = '12345678901';
```

### 6. 在索引列上使用函数

```sql
-- ❌ 索引失效
SELECT * FROM users WHERE UPPER(name) = 'ALICE';
SELECT * FROM orders WHERE YEAR(create_time) = 2024;

-- ✅ 改写查询
SELECT * FROM orders WHERE create_time >= '2024-01-01'
  AND create_time < '2025-01-01';
```

### 7. 联合索引不满足最左前缀

```sql
-- 索引：(a, b, c)
-- ❌ 索引失效
SELECT * FROM table WHERE b = 1 AND c = 2;
```

### 8. 范围查询右边的列

```sql
-- 索引：(a, b, c)
-- 只有 a 和 b 生效，c 不生效
SELECT * FROM table WHERE a = 1 AND b > 2 AND c = 3;
```

---

## 索引优化策略

### 1. 使用 EXPLAIN 分析查询

```sql
EXPLAIN SELECT * FROM users WHERE name = 'Alice';
```

**关键指标**：

- `type`：访问类型（system > const > eq_ref > ref > range > index > ALL）
- `key`：实际使用的索引
- `rows`：扫描的行数
- `Extra`：额外信息（Using index, Using filesort 等）

### 2. 优化 ORDER BY 和 GROUP BY

```sql
-- ✅ 利用索引的有序性
CREATE INDEX idx_age ON users(age);
SELECT * FROM users ORDER BY age;  -- 不需要额外排序
```

### 3. 避免 SELECT \*

```sql
-- ❌ 可能需要回表
SELECT * FROM users WHERE name = 'Alice';

-- ✅ 覆盖索引
SELECT id, name FROM users WHERE name = 'Alice';
```

### 4. 使用索引提示（Index Hint）

```sql
-- 强制使用某个索引
SELECT * FROM users USE INDEX(idx_name) WHERE name = 'Alice';

-- 忽略某个索引
SELECT * FROM users IGNORE INDEX(idx_age) WHERE age > 20;
```

### 5. 定期维护索引

```sql
-- 重建索引
ALTER TABLE users DROP INDEX idx_name;
CREATE INDEX idx_name ON users(name);

-- 或者
ALTER TABLE users ENGINE=InnoDB;  -- 重建表和索引

-- 分析表
ANALYZE TABLE users;

-- 优化表
OPTIMIZE TABLE users;
```

### 6. 监控索引使用情况

```sql
-- 查看未使用的索引
SELECT * FROM sys.schema_unused_indexes;

-- 查看重复的索引
SELECT * FROM sys.schema_redundant_indexes;
```

---

## 实际应用场景

### 场景 1：分页查询优化

```sql
-- ❌ 深分页性能差（需要扫描大量数据）
SELECT * FROM users ORDER BY id LIMIT 1000000, 10;

-- ✅ 使用子查询优化（覆盖索引）
SELECT * FROM users WHERE id >=
  (SELECT id FROM users ORDER BY id LIMIT 1000000, 1)
LIMIT 10;

-- ✅ 使用延迟关联
SELECT * FROM users a
INNER JOIN (
  SELECT id FROM users ORDER BY id LIMIT 1000000, 10
) b ON a.id = b.id;
```

### 场景 2：联合索引顺序选择

```sql
-- 假设查询条件：
-- WHERE a = ? AND b = ? AND c = ?
--
-- 选择索引顺序的原则：
-- 1. 等值查询的列放前面
-- 2. 选择性高的列放前面
-- 3. 考虑最左前缀的复用

-- 如果经常查询：
-- WHERE a = ?
-- WHERE a = ? AND b = ?
-- WHERE a = ? AND b = ? AND c = ?
--
-- 则建立：INDEX(a, b, c)
```

### 场景 3：范围查询 + 排序优化

```sql
-- 场景：查询某个时间范围内的数据并排序
SELECT * FROM orders
WHERE create_time >= '2024-01-01'
  AND create_time < '2024-02-01'
ORDER BY create_time;

-- ✅ 建立索引
CREATE INDEX idx_create_time ON orders(create_time);
-- 既能用于范围查询，又能避免排序
```

### 场景 4：多条件查询优化

```sql
-- 场景：经常同时查询姓名和年龄
SELECT * FROM users WHERE name = 'Alice' AND age = 25;

-- ✅ 建立联合索引
CREATE INDEX idx_name_age ON users(name, age);

-- 如果经常单独查询 name，这个索引也能使用（最左前缀）
SELECT * FROM users WHERE name = 'Alice';
```

### 场景 5：覆盖索引优化

```sql
-- 场景：统计查询
SELECT COUNT(*) FROM users WHERE age > 18;

-- ✅ 如果 age 有索引，直接扫描索引树
CREATE INDEX idx_age ON users(age);

-- 场景：只查询少数几个字段
SELECT id, name, age FROM users WHERE name = 'Alice';

-- ✅ 建立覆盖索引
CREATE INDEX idx_name_age ON users(name, age);
-- id 是主键，会包含在非聚簇索引中
```

### 场景 6：IN 查询优化

```sql
-- IN 查询可以使用索引
SELECT * FROM users WHERE id IN (1, 2, 3, 4, 5);

-- 但要注意 IN 的值不要太多（建议 < 1000）
-- 太多可能导致优化器放弃使用索引
```

---

## 总结

### 核心要点

1. **B+Tree** 是 MySQL InnoDB 的默认索引结构，适合范围查询和排序
2. **聚簇索引** 决定了数据的物理存储顺序，一个表只能有一个
3. **覆盖索引** 可以避免回表，是重要的优化手段
4. **最左前缀原则** 是联合索引使用的关键
5. **索引不是越多越好**，要权衡查询性能和写入性能

### 优化思路

1. 分析慢查询日志，找出需要优化的查询
2. 使用 EXPLAIN 分析执行计划
3. 根据查询特点设计合理的索引
4. 避免索引失效的情况
5. 使用覆盖索引减少回表
6. 定期维护和监控索引使用情况

### 最佳实践

- ✅ 在 WHERE、JOIN、ORDER BY、GROUP BY 的列上建索引
- ✅ 选择区分度高的列建索引
- ✅ 使用覆盖索引避免回表
- ✅ 联合索引遵循最左前缀原则
- ✅ 定期分析和优化索引
- ❌ 不要在频繁更新的列上建过多索引
- ❌ 不要在低区分度的列上建索引
- ❌ 不要让索引失效（函数、类型转换等）

---

## 参考资料

- MySQL 官方文档：[Optimization and Indexes](https://dev.mysql.com/doc/refman/8.0/en/optimization-indexes.html)
- 《高性能 MySQL》
- 《MySQL 技术内幕：InnoDB 存储引擎》
