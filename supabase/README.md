# Supabase 数据库配置指南

## 1. 创建 Supabase 项目

1. 访问 [Supabase](https://supabase.com)
2. 创建新项目
3. 记录项目的 URL 和 API Keys

## 2. 启用 pgvector 扩展

在 Supabase Dashboard 中:
1. 进入 SQL Editor
2. 运行以下命令:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

## 3. 执行数据库迁移

1. 在 Supabase SQL Editor 中执行 `schema.sql`
2. 执行 `policies.sql` 设置权限策略

## 4. 配置 Storage

1. 在 Supabase Dashboard 中创建 Storage Bucket:
   - 名称: `documents`
   - 公开访问: 根据需求设置

## 5. 更新环境变量

将以下信息填入 `.env` 文件:
```
SUPABASE_URL=your_project_url
SUPABASE_ANON_KEY=your_anon_key
SUPABASE_SERVICE_KEY=your_service_key
```

## 数据库表结构说明

### graphs
存储生成的 Mermaid 图表信息和 embedding

### nodes
存储图表中的节点信息

### edges
存储节点之间的连接关系

### documents
存储用户上传的文档和 embedding

### exports
存储导出任务的状态和路径
