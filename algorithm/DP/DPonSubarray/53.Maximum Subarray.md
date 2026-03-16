# 53. Maximum Subarray（最大子数组和）

> LeetCode 53 | 难度：Medium  
> 时间复杂度：O(n) | 空间复杂度：O(1)

---

## 一、问题描述

给定一个整数数组 `nums`，找到具有最大和的连续子数组，返回其最大和。

```
输入：nums = [-2, 1, -3, 4, -1, 2, 1, -5, 4]
输出：6
解释：连续子数组 [4, -1, 2, 1] 的和最大，为 6
```

---

## 二、核心思想：Prefix Sum + 最小前缀

### 数学推导

```
prefix[j] = nums[0] + nums[1] + ... + nums[j]

subarray(i+1 ... j) = prefix[j] - prefix[i]
```

**关键洞察**：任意子数组的和 = 两个前缀和的差

要让 `prefix[j] - prefix[i]` 最大化：
- `prefix[j]` → 当前值（遍历时固定）
- `prefix[i]` → 选择历史最小值

### 算法策略

遍历数组时：
1. 维护当前前缀和 `current_sum`
2. 维护历史最小前缀和 `min_prefix`
3. 每一步计算 `current_sum - min_prefix` 作为候选最大值

---

## 三、变量含义

| 变量 | 含义 | 数学表示 |
|------|------|----------|
| `max_so_far` | 当前找到的最大子数组和 | 最终答案 |
| `current_sum` | 当前的前缀和 | `prefix[j]` |
| `min_prefix` | 目前为止最小的前缀和 | `min(prefix[0..i])` |

---

## 四、完整代码

```javascript
function maxSubArray(nums) {
    let max_so_far = nums[0];   // 最大子数组和
    let current_sum = 0;         // 当前前缀和
    let min_prefix = 0;          // 历史最小前缀和

    for (let n of nums) {
        // Step 1: 累加当前前缀和
        current_sum += n;
        
        // Step 2: 计算当前子数组最大值
        // subarray_sum = prefix[j] - min(prefix[0..i])
        max_so_far = Math.max(max_so_far, current_sum - min_prefix);
        
        // Step 3: 更新最小前缀和
        min_prefix = Math.min(min_prefix, current_sum);
    }
    
    return max_so_far;
}
```

---

## 五、执行过程示例

以 `nums = [-2, 1, -3, 4, -1, 2, 1, -5, 4]` 为例：

| i | n | current_sum | min_prefix | current_sum - min_prefix | max_so_far |
|---|---|-------------|------------|--------------------------|------------|
| 0 | -2 | -2 | 0 | -2 | -2 |
| 1 | 1 | -1 | -2 | 1 | 1 |
| 2 | -3 | -4 | -2 | -2 | 1 |
| 3 | 4 | 0 | -4 | 4 | 4 |
| 4 | -1 | -1 | -4 | 3 | 4 |
| 5 | 2 | 1 | -4 | 5 | 5 |
| 6 | 1 | 2 | -4 | 6 | **6** |
| 7 | -5 | -3 | -4 | 1 | 6 |
| 8 | 4 | 1 | -4 | 5 | 6 |

最终答案：**6**

---

## 六、与 Kadane 算法对比

| 方面 | Prefix Sum 思路 | Kadane 算法 |
|------|----------------|-------------|
| 思维方式 | 数学：前缀差最大化 | 贪心：重新开始 vs 继续累加 |
| 核心公式 | `max(prefix[j] - min_prefix)` | `max(nums[i], current + nums[i])` |
| 时间复杂度 | O(n) | O(n) |
| 空间复杂度 | O(1) | O(1) |

### Kadane 算法代码

```javascript
function maxSubArray(nums) {
    let current = nums[0];
    let max_sum = nums[0];
    
    for (let i = 1; i < nums.length; i++) {
        current = Math.max(nums[i], current + nums[i]);
        max_sum = Math.max(max_sum, current);
    }
    
    return max_sum;
}
```

**两者本质等价**，只是看问题的角度不同：
- Prefix Sum：从"区间和 = 前缀差"的数学角度
- Kadane：从"是否重新开始"的贪心角度

---

## 七、总结

1. **核心公式**：`子数组和 = prefix[j] - prefix[i]`
2. **最大化策略**：固定 `prefix[j]`，最小化 `prefix[i]`
3. **一次遍历**：边走边记录最小前缀，边计算最大差值
4. **适用场景**：这种思路可扩展到「最大子数组乘积」「股票买卖」等问题
