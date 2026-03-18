# 工作流迁移工具 — 通用化架构规划

## 一、现状分析

### 当前架构（单向、平台耦合）

```
Google Dialogflow CX
        │
   [Step 0-1] 数据提取 & 标准化
        │
   [Step 2-5] 转换为中间格式（目前内嵌 Dyna.ai 节点类型）
        │
   [Step 6-8] 生成平台专用 JSON（Dyna.ai / AgentStudio 格式）
        │
   [Step 9]   上传到 Dyna.ai  ← 已移除
```

**主要问题：**
- Step 2-8 的节点类型（`textReply`、`captureUserReply`、`semanticJudgment` 等）均为 Dyna.ai 专有格式
- 整个 pipeline 是单向的：仅支持 Google CX → Dyna.ai
- 无法复用到 n8n、Dify、Coze 等平台
- 源平台和目标平台的逻辑高度耦合

---

## 二、目标架构（多源、多目标、可互换）

### 设计原则

1. **引入"通用中间格式（UIF）"**：所有平台先转换为统一中间格式，再从中间格式转换到目标平台
2. **解耦源转换器与目标转换器**：每个平台只需实现 Reader（读）或 Writer（写）
3. **插件化**：新增平台时只需新增一个 Reader 或 Writer，无需改动核心逻辑
4. **双向转换**：任意平台 → 中间格式 → 任意平台

### 目标架构图

```
┌─────────────────────────────────────────────────────────┐
│                      源平台 (Reader)                     │
│  Google CX │  n8n  │  Dify  │  Coze  │ 其他...          │
└────────────────────────┬────────────────────────────────┘
                         │  转换为通用中间格式（UIF）
                         ▼
┌─────────────────────────────────────────────────────────┐
│           Universal Intermediate Format (UIF)            │
│                   通用工作流中间格式                       │
│  - flows[]           (对话流列表)                         │
│  - nodes[]           (通用节点列表)                       │
│  - edges[]           (节点连接关系)                       │
│  - variables[]       (变量定义)                           │
│  - intents[]         (意图定义)                           │
│  - entities[]        (实体定义)                           │
└────────────────────────┬────────────────────────────────┘
                         │  从通用格式转换为目标平台格式
                         ▼
┌─────────────────────────────────────────────────────────┐
│                      目标平台 (Writer)                    │
│  n8n JSON │ Dify YAML │ Coze JSON │ Dyna.ai │ 其他...    │
└─────────────────────────────────────────────────────────┘
```

---

## 三、通用中间格式（UIF）设计

```json
{
  "version": "1.0",
  "meta": {
    "name": "Agent 名称",
    "description": "描述",
    "language": "en",
    "source_platform": "google_cx",
    "created_at": "2026-03-17T00:00:00Z"
  },
  "flows": [
    {
      "id": "flow_001",
      "name": "主流程",
      "is_main": true,
      "nodes": [
        {
          "id": "node_001",
          "type": "message",       // 通用节点类型（见下方节点类型表）
          "label": "欢迎消息",
          "data": {
            "text": "你好，请问有什么可以帮助你？",
            "language": "en"
          },
          "position": { "x": 100, "y": 200 }
        },
        {
          "id": "node_002",
          "type": "intent_router",
          "label": "意图判断",
          "data": {
            "intents": ["intent_001", "intent_002"],
            "fallback_node": "node_fallback"
          }
        }
      ],
      "edges": [
        {
          "id": "edge_001",
          "source": "node_001",
          "target": "node_002",
          "condition": null
        }
      ]
    }
  ],
  "intents": [
    {
      "id": "intent_001",
      "name": "CardServicing_CheckBalance",
      "training_phrases": ["查余额", "我的账户余额是多少"]
    }
  ],
  "entities": [
    {
      "id": "entity_001",
      "name": "card_type",
      "values": ["信用卡", "储蓄卡", "借记卡"]
    }
  ],
  "variables": [
    {
      "id": "var_001",
      "name": "user_card_type",
      "type": "string",
      "default": ""
    }
  ]
}
```

### 通用节点类型映射表

| UIF 节点类型 | 含义 | Google CX 对应 | n8n 对应 | Dify 对应 | Coze 对应 |
|---|---|---|---|---|---|
| `start` | 开始节点 | Start | Start Node | Start | 开始 |
| `message` | 发送文本消息 | Fulfillment Text | Send Message | Answer | 文本消息 |
| `input` | 等待用户输入 | Slot Filling | Wait Node | Question | 用户输入 |
| `intent_router` | 意图路由分发 | Route/Page Jump | Switch Node | Intent Classifier | 意图路由 |
| `condition` | 条件判断 | Condition Route | IF Node | IF/ELSE | 条件分支 |
| `code` | 执行代码/函数 | Webhook | Function Node | Code | 代码 |
| `llm` | LLM 调用 | Generative fallback | OpenAI Node | LLM | LLM |
| `jump` | 跳转到其他流 | Page Transition | Sub-workflow | Sub-flow | 跳转 |
| `end` | 结束对话 | End Session | End Node | End | 结束 |
| `variable_assign` | 变量赋值 | Parameter Fill | Set Node | Variable Assigner | 变量赋值 |
| `knowledge_query` | 知识库查询 | Knowledge Connector | Retrieval Node | Knowledge Retrieval | 知识库 |
| `webhook` | 调用外部 API | Webhook | HTTP Request | HTTP Request | HTTP请求 |

---

## 四、新目录结构规划

```
workflow-migration-tool/
│
├── 📂 readers/                        # 源平台读取器（Reader）
│   ├── __init__.py
│   ├── base_reader.py                 # 抽象基类
│   ├── google_cx/                     # Google Dialogflow CX
│   │   ├── __init__.py
│   │   ├── reader.py                  # 主读取器（整合现有 Step 0-5）
│   │   └── ...（现有 step0-step5 相关代码）
│   ├── n8n/                           # n8n（待实现）
│   │   └── reader.py
│   └── dify/                          # Dify（待实现）
│       └── reader.py
│
├── 📂 writers/                        # 目标平台输出器（Writer）
│   ├── __init__.py
│   ├── base_writer.py                 # 抽象基类
│   ├── n8n/                           # n8n JSON 格式（优先实现）
│   │   ├── __init__.py
│   │   └── writer.py
│   ├── dify/                          # Dify YAML 格式
│   │   └── writer.py
│   ├── coze/                          # Coze JSON 格式
│   │   └── writer.py
│   └── dyna_ai/                       # Dyna.ai（保留兼容）
│       └── writer.py
│
├── 📂 core/                           # 核心转换逻辑
│   ├── __init__.py
│   ├── uif_model.py                   # UIF 数据模型（Pydantic）
│   ├── converter.py                   # 统一调度入口
│   └── layout.py                      # 节点布局算法（现有 dagre）
│
├── 📂 api/                            # API 服务层
│   ├── server.py                      # FastAPI 服务（现 run_all_steps_server.py）
│   └── schemas.py                     # API 请求/响应模型
│
├── 📂 step2/                          # 现有 Step 2 模块（暂保留，未来迁移到 readers/google_cx）
├── 📂 layout_tools/
├── 📂 docs/
│
├── step0_extract_from_exported_flow.py  # 暂保留（未来整合进 readers/google_cx）
├── step1_process_dialogflow_data.py
├── ...（其余步骤文件）
│
├── run_all_steps_server.py
├── run_all_steps_api.py               # 已更新为通用接口
├── ROADMAP.md
└── .env
```

---

## 五、分阶段实施计划

### Phase 1：当前（已完成）
- [x] 移除 Step 9（Dyna.ai 上传接口）
- [x] 解除对 Dyna.ai 专用 API 凭证的强依赖
- [x] 通用化服务器和 API 脚本（不再绑定 Dyna.ai）
- [x] 更新 `.env` 配置

### Phase 2：定义 UIF（通用中间格式）
- [ ] 设计并实现 `core/uif_model.py`（Pydantic 数据模型）
- [ ] 将现有 Step 2-5 的输出（`nodes_config_*.json` + `edge_config_*.json`）适配为 UIF 格式
- [ ] 编写 UIF 格式校验工具

### Phase 3：重构 Google CX Reader
- [ ] 将 Step 0-5 整合为 `readers/google_cx/reader.py`
- [ ] 统一接口：`GoogleCXReader.read(file_path) -> UIF`
- [ ] 保持向后兼容（保留原始 step 脚本作为遗留调用）

### Phase 4：实现 n8n Writer（优先）
- [ ] 研究 n8n workflow JSON 格式规范
- [ ] 实现 `writers/n8n/writer.py`：`N8nWriter.write(uif: UIF) -> dict`
- [ ] 支持 n8n 节点类型：Trigger、Message、Switch、HTTP Request、Code、SubWorkflow
- [ ] 测试用例：Google CX → UIF → n8n JSON

### Phase 5：实现 Dify Writer
- [ ] 研究 Dify chatflow/workflow YAML 格式
- [ ] 实现 `writers/dify/writer.py`
- [ ] 测试：Google CX → UIF → Dify YAML

### Phase 6：实现 Coze Writer
- [ ] 研究 Coze Bot 工作流 JSON 格式
- [ ] 实现 `writers/coze/writer.py`
- [ ] 测试：Google CX → UIF → Coze JSON

### Phase 7：n8n Reader（实现双向）
- [ ] 实现 `readers/n8n/reader.py`：n8n JSON → UIF
- [ ] 支持 n8n → Dify、n8n → Coze 等跨平台转换

### Phase 8：统一 API 与 Web UI
- [ ] 更新 FastAPI 接口：支持指定 source_platform 和 target_platform
- [ ] 添加 Web UI（可选）：上传文件、选择源/目标平台、下载结果

---

## 六、下一步行动建议

1. **优先完成 n8n Writer**：n8n 是最流行的开源工作流平台，格式公开、社区活跃，是最值得优先支持的目标平台
2. **整理 UIF 节点类型**：先把现有的 Dyna.ai 节点类型（`textReply`、`captureUserReply` 等）映射到 UIF 通用类型
3. **研究目标平台格式**：分别导出一个简单的 n8n、Dify、Coze 工作流 JSON，作为格式参考
4. **逐步迁移**：不必一次性重构，可以边开发新 Writer 边保留旧代码，最终替换

---

## 七、平台格式参考资源

| 平台 | 工作流格式 | 参考文档 |
|---|---|---|
| n8n | JSON（workflow export） | https://docs.n8n.io/api/ |
| Dify | YAML/JSON（DSL export） | https://docs.dify.ai |
| Coze | JSON（Bot export） | https://www.coze.com/docs |
| Google CX | JSON（Agent export） | https://cloud.google.com/dialogflow/cx/docs |
