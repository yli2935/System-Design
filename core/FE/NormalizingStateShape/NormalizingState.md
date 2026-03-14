# Normalizing State Shape（状态规范化）

> 参考：[Redux - Normalizing State Shape](https://redux.js.org/usage/structuring-reducers/normalizing-state-shape)

## 概述

在前端应用中，我们经常处理**嵌套**或**关系型**数据。状态规范化是一种将嵌套数据结构"扁平化"的技术，类似于关系型数据库的设计思想。

---

## 问题：嵌套数据的痛点

### 典型的嵌套数据结构

```javascript
const blogPosts = [
  {
    id: 'post1',
    author: { username: 'user1', name: 'User 1' },
    body: '......',
    comments: [
      {
        id: 'comment1',
        author: { username: 'user2', name: 'User 2' },
        comment: '.....'
      },
      {
        id: 'comment2',
        author: { username: 'user3', name: 'User 3' },
        comment: '.....'
      }
    ]
  },
  // ... 更多文章
]
```

### 存在的问题

| 问题 | 描述 |
|------|------|
| **数据重复** | 同一个 User 可能在多处重复出现，更新时需要修改多个地方 |
| **Reducer 复杂** | 更新深层嵌套字段需要复杂的展开操作 |
| **性能问题** | 不可变更新需要复制所有祖先节点，导致不相关的组件重新渲染 |
| **查找困难** | 要找某条评论，需要遍历所有文章的所有评论 |

```javascript
// ❌ 更新深层嵌套数据非常痛苦
const newState = {
  ...state,
  posts: state.posts.map(post => 
    post.id === 'post1' 
      ? {
          ...post,
          comments: post.comments.map(comment =>
            comment.id === 'comment1'
              ? { ...comment, text: 'updated' }
              : comment
          )
        }
      : post
  )
}
```

---

## 解决方案：Normalized State（规范化状态）

### 核心原则

1. **每种数据类型有独立的"表"** - 类似数据库的表结构
2. **使用对象存储，ID 作为键** - 而不是数组
3. **通过 ID 引用关联数据** - 而不是嵌套对象
4. **用数组表示顺序** - `allIds` 数组维护排序

### 规范化后的结构

```javascript
{
  posts: {
    byId: {
      post1: {
        id: 'post1',
        author: 'user1',        // 只存 ID，不存完整对象
        body: '......',
        comments: ['comment1', 'comment2']  // 只存 ID 数组
      },
      post2: {
        id: 'post2',
        author: 'user2',
        body: '......',
        comments: ['comment3', 'comment4', 'comment5']
      }
    },
    allIds: ['post1', 'post2']  // 维护顺序
  },
  
  comments: {
    byId: {
      comment1: { id: 'comment1', author: 'user2', comment: '.....' },
      comment2: { id: 'comment2', author: 'user3', comment: '.....' },
      comment3: { id: 'comment3', author: 'user3', comment: '.....' },
      // ...
    },
    allIds: ['comment1', 'comment2', 'comment3', 'comment4', 'comment5']
  },
  
  users: {
    byId: {
      user1: { username: 'user1', name: 'User 1' },
      user2: { username: 'user2', name: 'User 2' },
      user3: { username: 'user3', name: 'User 3' }
    },
    allIds: ['user1', 'user2', 'user3']
  }
}
```

### 规范化的优势

| 优势 | 说明 |
|------|------|
| **单一数据源** | 每个实体只在一个地方定义，更新简单 |
| **扁平结构** | Reducer 逻辑简单，不需要深层嵌套 |
| **O(1) 查找** | 通过 ID 直接访问：`state.users.byId[userId]` |
| **精确更新** | 只影响变化的部分，减少不必要的重渲染 |
| **易于缓存** | 可以轻松实现实体级别的缓存和去重 |

---

## 状态结构设计

### 推荐的整体结构

```javascript
{
  // 领域数据（非关系型）
  simpleDomainData1: { ... },
  simpleDomainData2: { ... },
  
  // 实体数据（关系型，规范化）
  entities: {
    users: { byId: {}, allIds: [] },
    posts: { byId: {}, allIds: [] },
    comments: { byId: {}, allIds: [] }
  },
  
  // UI 状态
  ui: {
    isLoading: false,
    selectedPostId: null,
    modalOpen: false
  }
}
```

### 处理多对多关系

使用**关联表**（Join Table）：

```javascript
{
  entities: {
    authors: {
      byId: { /* ... */ },
      allIds: []
    },
    books: {
      byId: { /* ... */ },
      allIds: []
    },
    // 关联表：作者-书籍 多对多关系
    authorBook: {
      byId: {
        1: { id: 1, authorId: 5, bookId: 22 },
        2: { id: 2, authorId: 5, bookId: 15 },
        3: { id: 3, authorId: 42, bookId: 12 }
      },
      allIds: [1, 2, 3]
    }
  }
}

// 查找某作者的所有书籍
const getBooksByAuthor = (state, authorId) => {
  return Object.values(state.entities.authorBook.byId)
    .filter(ab => ab.authorId === authorId)
    .map(ab => state.entities.books.byId[ab.bookId])
}
```

---

## 实现工具

### 1. Normalizr

[Normalizr](https://github.com/paularmstrong/normalizr) 是最常用的规范化库，可以自动将嵌套 API 响应转换为规范化结构。

```javascript
import { normalize, schema } from 'normalizr'

// 定义 Schema
const user = new schema.Entity('users')
const comment = new schema.Entity('comments', {
  author: user
})
const post = new schema.Entity('posts', {
  author: user,
  comments: [comment]
})

// API 返回的嵌套数据
const apiResponse = {
  id: 'post1',
  author: { id: 'user1', name: 'User 1' },
  comments: [
    { id: 'comment1', author: { id: 'user2', name: 'User 2' }, text: '...' }
  ]
}

// 规范化
const normalized = normalize(apiResponse, post)

// 结果
console.log(normalized)
// {
//   result: 'post1',
//   entities: {
//     users: { user1: {...}, user2: {...} },
//     comments: { comment1: {...} },
//     posts: { post1: {...} }
//   }
// }
```

### 2. Redux Toolkit - createEntityAdapter

Redux Toolkit 提供了 `createEntityAdapter` 简化规范化状态的管理：

```javascript
import { createEntityAdapter, createSlice } from '@reduxjs/toolkit'

// 创建 adapter
const postsAdapter = createEntityAdapter({
  // 自定义 ID 字段（默认是 'id'）
  selectId: (post) => post.id,
  // 排序
  sortComparer: (a, b) => b.date.localeCompare(a.date)
})

// 初始状态
const initialState = postsAdapter.getInitialState({
  loading: false,
  error: null
})
// 生成: { ids: [], entities: {}, loading: false, error: null }

// 创建 Slice
const postsSlice = createSlice({
  name: 'posts',
  initialState,
  reducers: {
    // Adapter 提供的 CRUD 方法
    addPost: postsAdapter.addOne,
    addPosts: postsAdapter.addMany,
    updatePost: postsAdapter.updateOne,
    removePost: postsAdapter.removeOne,
    setPosts: postsAdapter.setAll
  }
})

// 自动生成的 Selectors
const postsSelectors = postsAdapter.getSelectors(state => state.posts)
// postsSelectors.selectAll(state)     - 获取所有
// postsSelectors.selectById(state, id) - 按 ID 获取
// postsSelectors.selectIds(state)      - 获取所有 ID
// postsSelectors.selectTotal(state)    - 获取总数
```

---

## 更新规范化数据

### 添加新实体

```javascript
// ✅ 简单直接
case 'ADD_COMMENT':
  return {
    ...state,
    comments: {
      byId: {
        ...state.comments.byId,
        [action.payload.id]: action.payload
      },
      allIds: [...state.comments.allIds, action.payload.id]
    }
  }
```

### 更新实体

```javascript
// ✅ 只需更新一个地方
case 'UPDATE_USER':
  return {
    ...state,
    users: {
      ...state.users,
      byId: {
        ...state.users.byId,
        [action.payload.id]: {
          ...state.users.byId[action.payload.id],
          ...action.payload.changes
        }
      }
    }
  }
```

### 删除实体

```javascript
case 'DELETE_POST':
  const { [action.payload]: deleted, ...remainingPosts } = state.posts.byId
  return {
    ...state,
    posts: {
      byId: remainingPosts,
      allIds: state.posts.allIds.filter(id => id !== action.payload)
    }
  }
```

---

## 与组件连接

### 设计模式：容器组件传递 ID

```jsx
// ✅ 推荐：父组件传递 ID，子组件自己获取数据
function PostList() {
  const postIds = useSelector(state => state.posts.allIds)
  
  return (
    <div>
      {postIds.map(id => (
        <PostItem key={id} postId={id} />  {/* 只传 ID */}
      ))}
    </div>
  )
}

function PostItem({ postId }) {
  // 子组件自己获取需要的数据
  const post = useSelector(state => state.posts.byId[postId])
  const author = useSelector(state => state.users.byId[post.author])
  
  return (
    <div>
      <h3>{post.title}</h3>
      <span>by {author.name}</span>
    </div>
  )
}
```

### 为什么这样更好？

```
传递完整对象          vs          传递 ID
─────────────────────────────────────────────────
父组件获取所有数据      │    父组件只获取 ID 列表
传递大对象给子组件      │    传递简单的字符串 ID
任何字段变化都重渲染    │    只有相关数据变化才重渲染
性能较差              │    性能优化
```

---

## 使用 Selectors 访问数据

### 基础 Selectors

```javascript
// 获取单个实体
export const selectUserById = (state, userId) => 
  state.entities.users.byId[userId]

// 获取所有实体
export const selectAllPosts = (state) => 
  state.entities.posts.allIds.map(id => state.entities.posts.byId[id])

// 获取关联数据
export const selectPostWithAuthor = (state, postId) => {
  const post = state.entities.posts.byId[postId]
  const author = state.entities.users.byId[post.author]
  return { ...post, author }
}
```

### 使用 Reselect 缓存

```javascript
import { createSelector } from 'reselect'

const selectPostsById = state => state.entities.posts.byId
const selectPostIds = state => state.entities.posts.allIds

// 缓存的 selector，避免重复计算
export const selectAllPosts = createSelector(
  [selectPostsById, selectPostIds],
  (byId, allIds) => allIds.map(id => byId[id])
)

// 带参数的 selector
export const selectPostsByAuthor = createSelector(
  [selectAllPosts, (state, authorId) => authorId],
  (posts, authorId) => posts.filter(post => post.author === authorId)
)
```

---

## 最佳实践总结

| 实践 | 说明 |
|------|------|
| **使用 `byId` + `allIds` 结构** | 标准的规范化模式 |
| **API 响应立即规范化** | 使用 Normalizr 或手动转换 |
| **使用 createEntityAdapter** | Redux Toolkit 提供的便捷工具 |
| **组件只连接需要的数据** | 传递 ID 而不是完整对象 |
| **使用 Selectors** | 封装数据访问逻辑，便于缓存和复用 |
| **保持实体独立** | 避免在实体中嵌套其他实体 |

---

## 何时使用规范化？

### ✅ 适合规范化

- 数据有明显的关系（一对多、多对多）
- 同一实体在多处被引用
- 需要频繁更新特定实体
- 应用规模较大，性能敏感

### ❌ 不需要规范化

- 简单的 UI 状态（模态框、加载状态）
- 一次性使用的数据
- 数据结构简单、没有关联关系
- 小型应用
