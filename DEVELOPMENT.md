# 开发指南

## 架构设计

### 整体流程

```
用户输入 Prompt
    ↓
LLM 生成 Mermaid 代码
    ↓
保存到数据库 (生成 embedding)
    ↓
RAG 搜索相关图表/文档
    ↓
建立 DAG 连接
    ↓
可视化展示
    ↓
导出 (PPT/PDF/Word)
```

### 数据流

1. **Prompt → Mermaid**
   - 用户输入自然语言描述
   - LangChain + OpenAI 生成 Mermaid 代码
   - 可选: 使用预定义模板优化

2. **Mermaid → Graph**
   - 解析 Mermaid 代码提取节点和边
   - 生成 embedding 向量
   - 保存到 Supabase

3. **RAG 搜索**
   - 对 prompt 生成 embedding
   - 使用 pgvector 进行相似度搜索
   - 返回相关图表和文档

4. **DAG 构建**
   - 基于相似度创建边
   - 计算权重和 RAG 分数
   - 构建邻接图

5. **可视化**
   - React Flow 渲染 DAG
   - Mermaid.js 渲染图表
   - D3.js 进行高级可视化

## API 设计

### 图表相关

```typescript
POST /api/graphs/generate
Body: { prompt: string }
Response: { graphId: string, mermaidCode: string }

GET /api/graphs/:id
Response: { id, title, prompt, mermaidCode, created_at }

GET /api/graphs/:id/nodes
Response: { nodes: [], edges: [] }

GET /api/graphs/:id/export/:format
Response: Blob (file download)
```

### 文档相关

```typescript
POST /api/documents/upload
Body: FormData (file)
Response: { id, filename, storage_path }

POST /api/documents/search
Body: { query: string }
Response: [{ id, filename, content, similarity }]
```

## 数据库设计

### 核心表

- **graphs**: 存储图表元数据和 embedding
- **nodes**: 图表中的节点
- **edges**: 节点之间的连接
- **documents**: 上传的文档
- **exports**: 导出任务记录

### 向量搜索

使用 pgvector 扩展:
- `embedding vector(1536)`: OpenAI embedding 维度
- `<=>` 运算符: 余弦距离
- `ivfflat` 索引: 加速搜索

## 前端组件

### PromptInput
- 用户输入 prompt
- 调用生成 API
- 显示加载状态

### MermaidViewer
- 渲染 Mermaid 图表
- 支持缩放和导出
- 错误处理

### GraphViewer
- 展示 DAG 关系图
- 使用 React Flow
- 支持交互操作

### ExportPanel
- 多格式导出
- 下载管理
- 进度显示

## 后端服务

### LLM Service
- 封装 OpenAI API 调用
- Prompt 工程
- 错误重试

### Graph Service
- 图表 CRUD
- RAG 搜索
- DAG 构建

### Document Service
- 文件上传
- 内容解析
- Embedding 生成

### Export Service
- PPT 生成 (PptxGenJS)
- PDF 生成 (Puppeteer)
- Word 生成 (docx)

## 环境配置

### 必需的环境变量

```bash
# Supabase
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_KEY=

# OpenAI
OPENAI_API_KEY=

# Server
PORT=3001
NODE_ENV=development
FRONTEND_URL=http://localhost:5173
```

## 部署

### 前端部署
- 构建: `npm run build --prefix frontend`
- 部署到 Vercel/Netlify
- 配置环境变量

### 后端部署
- 构建: `npm run build --prefix backend`
- 部署到 Railway/Render
- 配置环境变量

### 数据库
- 使用 Supabase 托管
- 执行迁移脚本
- 配置 Storage

## 测试

### 单元测试
```bash
npm test
```

### E2E 测试
```bash
npm run test:e2e
```

## 性能优化

1. **向量搜索优化**
   - 使用 ivfflat 索引
   - 调整 match_threshold
   - 限制 match_count

2. **缓存策略**
   - Redis 缓存热门查询
   - 浏览器缓存静态资源

3. **并发处理**
   - Bull 队列处理导出任务
   - 异步生成 embedding

## 安全考虑

1. **API 安全**
   - Rate limiting
   - Input validation
   - CORS 配置

2. **数据安全**
   - Row Level Security
   - 文件上传限制
   - SQL 注入防护

3. **认证授权**
   - Supabase Auth
   - JWT token
   - 权限控制
