# Mermaid RAG Graph

基于 Prompt 生成 Mermaid 图表，结合 RAG 搜索和 DAG 可视化的 Web 应用。

## 功能特性

- 🎨 **智能图表生成**: 通过自然语言描述生成 Mermaid 图表
- 🔍 **RAG 搜索**: 基于向量相似度搜索相关图表和文档
- 🌐 **DAG 可视化**: 展示图表之间的关联关系
- 📤 **多格式导出**: 支持导出为 PPT、PDF、Word、JSON 等格式
- 📚 **知识库管理**: 支持上传文档构建知识库

## 技术栈

### 前端
- React 18 + TypeScript
- Vite
- TailwindCSS
- Mermaid.js
- React Flow (DAG 可视化)
- Zustand (状态管理)

### 后端
- Node.js + Express
- TypeScript
- LangChain.js
- OpenAI API

### 数据库
- Supabase (PostgreSQL + pgvector)
- 向量相似度搜索
- Realtime 订阅

### 导出功能
- PptxGenJS (PPT)
- Puppeteer (PDF)
- docx (Word)
- json2csv (数据导出)

## 项目结构

```
project_demo/
├── frontend/                 # 前端应用
│   ├── src/
│   │   ├── components/      # React 组件
│   │   ├── services/        # API 服务
│   │   └── App.tsx          # 主应用
│   └── package.json
├── backend/                  # 后端服务
│   ├── src/
│   │   ├── controllers/     # 控制器
│   │   ├── services/        # 业务逻辑
│   │   ├── routes/          # 路由
│   │   └── config/          # 配置
│   └── package.json
├── supabase/                 # 数据库配置
│   ├── schema.sql           # 数据库表结构
│   ├── policies.sql         # 权限策略
│   └── README.md            # 配置指南
└── package.json              # 根配置
```

## 快速开始

### 1. 安装依赖

```bash
npm run install:all
```

### 2. 配置环境变量

复制 `.env.example` 到 `.env` 并填入配置:

```bash
# 根目录
cp .env.example .env

# 前端
cp frontend/.env.example frontend/.env
```

需要配置:
- Supabase URL 和 API Keys
- OpenAI API Key

### 3. 配置 Supabase 数据库

参考 [supabase/README.md](supabase/README.md) 完成数据库配置。

### 4. 启动开发服务器

```bash
npm run dev
```

- 前端: http://localhost:5173
- 后端: http://localhost:3001

## 使用流程

1. **输入 Prompt**: 在输入框中描述你想要生成的图表
2. **生成图表**: 系统使用 LLM 生成 Mermaid 代码并渲染
3. **RAG 搜索**: 自动搜索相关的历史图表和文档
4. **DAG 联通**: 基于语义相似度建立图表之间的连接
5. **可视化展示**: 在 DAG 视图中查看关联关系
6. **导出**: 选择格式导出图表

## 核心功能实现

### Mermaid 生成
- 混合方式: LLM 生成 + 模板系统
- 支持多种图表类型 (流程图、时序图、类图等)

### RAG 搜索
- 使用 OpenAI Embeddings 生成向量
- pgvector 进行相似度搜索
- 支持文档上传和历史图表搜索

### DAG 联通
- 基于 embedding 相似度自动建立连接
- 支持手动调整节点关系
- 可视化展示图谱结构

## 待实现功能

- [ ] 完整的导出功能 (PPT/PDF/Word)
- [ ] React Flow 集成进行 DAG 可视化
- [ ] 文档上传和解析
- [ ] 用户认证和权限管理
- [ ] 图表编辑功能
- [ ] 批量操作和管理

## 开发命令

```bash
# 安装所有依赖
npm run install:all

# 启动开发环境 (前端 + 后端)
npm run dev

# 只启动前端
npm run dev:frontend

# 只启动后端
npm run dev:backend

# 构建生产版本
npm run build
```

## 贡献

欢迎提交 Issue 和 Pull Request!

## License

MIT
