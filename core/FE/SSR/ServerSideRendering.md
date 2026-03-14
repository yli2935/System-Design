# Server-Side Rendering (SSR) vs Client-Side Rendering (CSR)

## 概述

| 特性 | Server-Side Rendering (SSR) | Client-Side Rendering (CSR) |
|------|----------------------------|----------------------------|
| 渲染位置 | 服务器端 | 浏览器端 |
| 首次加载 | HTML 完整返回 | 返回空壳 HTML + JS |
| SEO 友好度 | ✅ 优秀 | ❌ 较差 |
| 首屏时间 (FCP) | ✅ 快 | ❌ 慢 |
| 可交互时间 (TTI) | 较慢 | 取决于 JS 大小 |
| 服务器压力 | ⬆️ 高 | ⬇️ 低 |

---

## Server-Side Rendering (SSR)

### 工作原理

```
用户请求 → 服务器执行渲染 → 返回完整HTML → 浏览器显示 → 下载JS → Hydration(激活)
```

### 优点

1. **SEO 友好** - 搜索引擎爬虫可以直接读取完整的 HTML 内容
2. **首屏加载快** - 用户能更快看到页面内容（First Contentful Paint 快）
3. **适合弱网环境** - 不依赖大量 JS 下载和执行
4. **社交媒体分享** - Open Graph 标签可以被正确解析

### 缺点

1. **服务器负载高** - 每次请求都需要服务器渲染
2. **TTFB 较长** - 服务器需要时间生成 HTML
3. **页面切换体验** - 可能出现白屏闪烁
4. **开发复杂度** - 需要处理服务端和客户端代码差异

### 适用场景

- 内容型网站（新闻、博客、电商）
- 需要 SEO 优化的页面
- 首屏性能要求高的应用
- 目标用户设备性能较差

---

## Hydration（水合/激活）

### 什么是 Hydration？

Hydration 是 SSR 中的关键步骤，指的是**将服务器渲染的静态 HTML 转变为可交互的动态页面**的过程。

可以理解为：服务器返回的是"干燥的"静态 HTML 骨架，JavaScript 加载后为其注入"水分"（事件监听器、状态管理等），使页面"活"起来。

### Hydration 工作流程

```
┌─────────────────────────────────────────────────────────────────┐
│                        完整 SSR + Hydration 流程                  │
└─────────────────────────────────────────────────────────────────┘

1. 用户请求页面
        ↓
2. 服务器执行 React/Vue 代码，生成 HTML 字符串
        ↓
3. 服务器返回完整 HTML（用户立即可见，但不可交互）
        ↓
4. 浏览器开始下载 JavaScript Bundle
        ↓
5. JavaScript 加载完成，开始 Hydration：
   ├── 对比服务器 HTML 与客户端虚拟 DOM
   ├── 绑定事件监听器（onClick, onChange 等）
   ├── 恢复组件状态
   └── 连接客户端路由
        ↓
6. 页面完全可交互 ✅
```

### Hydration 时间线示意

```
时间轴 ─────────────────────────────────────────────────────────────►

        │← TTFB →│←─── 页面可见但不可交互 ───→│←─ 完全可交互 ─→│
        
请求    服务器     HTML        JS 下载        Hydration      TTI
发出    渲染      到达        完成           完成           达到
 │       │        │            │              │              │
 ●───────●────────●────────────●──────────────●──────────────●
         ↑                     ↑              ↑
       FCP                  JS Ready     Interactive
    (首次内容绘制)
```

### Hydration Mismatch（不匹配问题）

当服务器渲染的 HTML 与客户端期望的结构不一致时，会发生 Hydration Mismatch。

**常见原因：**

```javascript
// ❌ 错误示例：服务端和客户端渲染结果不同
function Component() {
  // 时间在服务端和客户端执行时不同
  return <div>{new Date().toLocaleString()}</div>
}

// ❌ 错误示例：使用 window 对象
function Component() {
  // window 在服务端不存在
  return <div>{window.innerWidth}px</div>
}

// ❌ 错误示例：随机数
function Component() {
  return <div>{Math.random()}</div>
}
```

**解决方案：**

```javascript
// ✅ 正确做法：使用 useEffect 处理客户端特有逻辑
function Component() {
  const [width, setWidth] = useState(0)
  
  useEffect(() => {
    // 只在客户端执行
    setWidth(window.innerWidth)
  }, [])
  
  return <div>{width}px</div>
}

// ✅ 正确做法：使用 suppressHydrationWarning
function Component() {
  return (
    <div suppressHydrationWarning>
      {new Date().toLocaleString()}
    </div>
  )
}
```

### 传统 Hydration 的问题

| 问题 | 描述 |
|------|------|
| **全量 Hydration** | 即使页面只有一个按钮需要交互，也要 Hydrate 整个页面 |
| **阻塞渲染** | Hydration 过程中主线程被占用，页面可能卡顿 |
| **JS Bundle 大** | 需要下载完整的客户端代码才能开始 Hydration |
| **TTI 延迟** | 用户看到内容后很久才能交互（"恐怖谷"体验） |

### 现代优化方案

#### 1. Progressive Hydration（渐进式水合）

按需 Hydrate 页面的不同部分，优先处理可见区域。

```javascript
// 只有当组件进入视口时才 Hydrate
<LazyHydrate whenVisible>
  <HeavyComponent />
</LazyHydrate>
```

#### 2. Selective Hydration（选择性水合）

React 18 的特性，允许在部分 HTML 还在加载时就开始 Hydration。

```javascript
// React 18 Suspense + Selective Hydration
<Suspense fallback={<Loading />}>
  <Comments />  {/* 可以独立 Hydrate */}
</Suspense>
```

#### 3. Islands Architecture（岛屿架构）

页面大部分是静态 HTML，只有特定"岛屿"需要 JavaScript。

```
┌──────────────────────────────────────┐
│  静态 Header（纯 HTML，无需 JS）      │
├──────────────────────────────────────┤
│                                      │
│  ┌─────────┐        静态内容         │
│  │ 交互岛屿 │                        │
│  │ (Hydrate)│        (纯 HTML)       │
│  └─────────┘                         │
│                                      │
│         ┌──────────────┐             │
│         │   交互岛屿    │             │
│         │  (Hydrate)   │             │
│         └──────────────┘             │
├──────────────────────────────────────┤
│  静态 Footer（纯 HTML，无需 JS）      │
└──────────────────────────────────────┘
```

**代表框架：** Astro, Fresh (Deno), Qwik

#### 4. Resumability（可恢复性）

Qwik 框架的理念：不需要 Hydration，直接从服务器状态恢复。

```
传统 SSR:  服务器渲染 → 下载JS → 重新执行 → 绑定事件
Qwik:     服务器渲染 → 用户交互时才加载对应JS → 直接执行
```

### React 中的 Hydration API

```javascript
// React 17 及之前
import ReactDOM from 'react-dom'
ReactDOM.hydrate(<App />, document.getElementById('root'))

// React 18+
import { hydrateRoot } from 'react-dom/client'
hydrateRoot(document.getElementById('root'), <App />)
```

### 关键要点总结

| 概念 | 说明 |
|------|------|
| **Hydration** | 让服务器渲染的静态 HTML 变得可交互 |
| **Hydration Mismatch** | 服务端和客户端渲染结果不一致导致的错误 |
| **TTI Gap** | 用户看到内容到可以交互之间的延迟 |
| **Progressive Hydration** | 按需、分块地进行 Hydration |
| **Islands Architecture** | 只对需要交互的部分进行 Hydration |

---

## Client-Side Rendering (CSR)

### 工作原理

```
用户请求 → 返回空HTML壳 → 下载JS Bundle → JS执行渲染 → 显示内容
```

### 优点

1. **服务器压力小** - 服务器只返回静态文件，可使用 CDN
2. **交互体验好** - 页面切换流畅，无需重新加载
3. **前后端分离** - 开发职责清晰
4. **丰富的交互** - 适合构建复杂的单页应用 (SPA)

### 缺点

1. **SEO 不友好** - 爬虫可能无法正确索引内容
2. **首屏加载慢** - 需要下载并执行 JS 才能看到内容
3. **白屏时间长** - 初始 HTML 是空的
4. **依赖 JS** - 如果 JS 加载失败，页面无法显示

### 适用场景

- 后台管理系统
- 不需要 SEO 的 Web 应用
- 交互密集型应用
- 用户登录后才能访问的内容

---

## 混合方案

### SSG (Static Site Generation)

- 构建时生成静态 HTML
- 结合 SSR 的 SEO 优势和 CSR 的低服务器压力
- 适合内容不经常变化的网站

### ISR (Incremental Static Regeneration)

- 按需重新生成静态页面
- Next.js 引入的概念
- 兼顾静态性能和内容更新

### Streaming SSR

- 分块传输 HTML
- 用户可以更早看到部分内容
- React 18 支持的特性

---

## 性能指标对比

| 指标 | SSR | CSR |
|------|-----|-----|
| FCP (First Contentful Paint) | ✅ 快 | ❌ 慢 |
| LCP (Largest Contentful Paint) | ✅ 快 | ❌ 慢 |
| TTI (Time to Interactive) | ⚠️ 中等 | ⚠️ 取决于JS大小 |
| TTFB (Time to First Byte) | ❌ 较慢 | ✅ 快 |

---

## 技术框架

| 类型 | 框架示例 |
|------|---------|
| SSR | Next.js, Nuxt.js, Remix, SvelteKit |
| CSR | Create React App, Vue CLI, Vite |
| SSG | Gatsby, Astro, Hugo, Jekyll |

---

## 选择建议

```
需要 SEO？
├── 是 → 考虑 SSR 或 SSG
│   ├── 内容频繁更新 → SSR
│   └── 内容相对静态 → SSG
└── 否 → CSR 可能更合适
    ├── 后台系统 → CSR
    └── 登录后应用 → CSR
```
