# 工作流节点类型完整映射总表

> 以「通用中间格式（UIF）」节点类型为中轴，横向打通四个平台的对应关系。
> 来源：对 `step2/`、`step6_workflow_generator.py` 等核心代码的深度分析。
> 更新日期：2026-03-17

---

## 一、核心大表：Google CX → UIF → AgentStudio / n8n / Dify / Coze

> **说明**：
> - **Google CX 触发元素**：Dialogflow CX 中触发该转换的具体字段或结构
> - **UIF 节点类型**：通用中间格式（Universal Intermediate Format）中的抽象节点类型，是多平台互转的枢纽
> - **AgentStudio 节点链**：当前代码实际生成的节点序列（可能是单节点或多节点链路）
> - **n8n / Dify / Coze**：未来实现各 Writer 时的对应节点（部分为计划参考，待验证）

| # | Google CX 触发元素 | UIF 节点类型 | AgentStudio 节点链（当前实现） | n8n 对应节点 | Dify 对应节点 | Coze 对应节点 |
|:---:|---|:---:|---|---|---|---|
| 1 | Flow 入口 / Start Page | `start` | `start` | Trigger / Manual Trigger | Start | 开始节点 |
| 2 | `page.onLoad.responses`（静态文本回复） | `message` | `textReply` | Send Message Node | Answer Node | 文本消息节点 |
| 3 | `page.onLoad.responses` 含 `$sys.func.XXX()` 表达式 | `code` + `message` | `code` → `textReply` | Code Node → Send Message | Code → Answer | 代码节点 → 文本消息 |
| 4 | `page.onLoad.setParameterActions`（页面加载赋值） | `variable_assign` | `code` | Set Node | Variable Assigner | 变量赋值节点 |
| 5 | `page.slots`（槽位收集） | `input` + `llm` + `code` | `captureUserReply` → `llmVariableAssignment` → `code` | Wait/Form Node → AI Node → Code Node | Question → LLM → Code | 用户输入 → LLM → 代码 |
| 6 | `page.slots`（ENUMERATION 实体，枚举值提示） | `input` + `llm` + `code` | `captureUserReply` → `llmVariableAssignment`（含候选值 Hint）→ `code` | 同上（hints 注入 Prompt） | 同上 | 同上 |
| 7 | `page.slots`（KIND_REGEXP 实体，格式校验） | `input` + `llm` + `code` | `captureUserReply` → `llmVariableAssignment`（含格式描述 Hint）→ `code` | 同上 | 同上 | 同上 |
| 8 | `transitionEvents` 含 `triggerIntentId`（意图路由 V2，当前默认） | `input` + `intent_router` | `captureUserReply` → `semanticJudgment` | Wait Node → Switch / AI Classifier | Question → Intent Classifier | 用户输入 → 意图路由 |
| 9 | `transitionEvents` 含 `triggerIntentId`（意图路由 V1，知识库模式） | `input` + `knowledge_query` + `code` + `condition` | `captureUserReply` → `knowledgeAssignment` → `code` → `condition` | Wait Node → Retrieval → Code → IF | Question → Knowledge Retrieval → Code → IF/ELSE | 用户输入 → 知识库 → 代码 → 条件 |
| 10 | `transitionEvents` 意图有 `parameters`（参数提取） | `llm` + `code` | `llmVariableAssignment` → `code` | AI Node → Code Node | LLM → Code | LLM → 代码 |
| 11 | `transitionEvents` 意图有 `parameters` + 条件分支 | `llm` + `code` + `condition` | `llmVariableAssignment` → `code` → `condition` | AI Node → Code → IF | LLM → Code → IF/ELSE | LLM → 代码 → 条件 |
| 12 | `transitionEvents.condition`（单一条件 `x = "v"`） | `condition` | `condition`（单分支） | IF Node | IF/ELSE | 条件节点 |
| 13 | `transitionEvents.conditionString` AND 多条件 | `condition` | `condition`（多条件，`logical_operator: "and"`） | IF Node（多条件） | IF/ELSE（AND） | 条件节点 |
| 14 | `transitionEvents.conditionString` OR 多条件 | `condition` | `condition`（多条件，`logical_operator: "or"`） | IF Node（多条件） | IF/ELSE（OR） | 条件节点 |
| 15 | `transitionEvents.conditionString` 混合 AND+OR | `code` + `condition` | `code`（混合条件计算）→ `condition` | Code → IF | Code → IF/ELSE | 代码 → 条件 |
| 16 | `transitionEvents.conditionString` 字面量 `true`/`false` | `code` + `condition` | `code`（字面量条件）→ `condition` | 直接连边 | 直接连边 | 直接连边 |
| 17 | `transitionEvents` 空条件（始终为 true） | _(直接连边)_ | 直接生成边，跳过 condition 节点 | 直接连线 | 直接连线 | 直接连线 |
| 18 | `transitionEvents.handler.targetPageId`（页面跳转） | `jump` | `jump`（`jump_type: "flow"`） | Execute Workflow Node | Sub-flow | 跳转节点 |
| 19 | `transitionEvents.handler.targetFlowId`（Flow 跳转） | `jump` | `jump`（`jump_type: "flow"`） | Execute Workflow Node | Sub-flow | 跳转节点 |
| 20 | `beforeTransition.setParameterActions`（跳转前赋值） | `variable_assign` | `code`（Set Parameters） | Set Node | Variable Assigner | 变量赋值 |
| 21 | `beforeTransition.staticUserResponse`（跳转前回复） | `message` | `textReply`（BeforeTransition_Response） | Send Message | Answer | 文本消息 |
| 22 | Fallback / 未匹配意图（default condition） | `llm` | `llMReply` | AI / OpenAI Node | LLM | LLM节点 |
| 23 | Flow 层级 slots（Start Page 的槽位） | `input` + `llm` + `code` | `captureUserReply` → `llmVariableAssignment` → `code` | 同 #5 | 同 #5 | 同 #5 |
| 24 | Flow 层级条件路由 | `condition` | `condition`（多分支） | IF Node | IF/ELSE | 条件节点 |
| 25 | `$session.params.xxx` 变量引用（在文本中） | _(变量插值)_ | `{{xxx}}` 富文本 span 格式 | `{{ $vars.xxx }}` | `{{xxx}}` | `{{xxx}}` |
| 26 | `$sys.func.GET_FIELD(...)` 系统函数 | `code` | `code`（表达式求值节点） | Code Node | Code | 代码节点 |
| 27 | _(隐含)_ 每个功能节点的 UI 包装 | _(容器)_ | `block`（透明容器，包裹每个功能节点） | _(无对应)_ | _(无对应)_ | _(无对应)_ |

---

## 二、UIF 节点类型定义（中间层完整列表）

| UIF 节点类型 | 含义 | 说明 |
|---|---|---|
| `start` | 开始节点 | 每个 Flow/Workflow 的固定入口 |
| `message` | 发送消息 | 向用户发送静态文本、按钮、富文本等 |
| `input` | 等待用户输入 | 暂停流程，接收用户下一条消息并存入变量 |
| `intent_router` | 意图路由 | 基于语义/向量模型识别用户意图，多分支分发 |
| `condition` | 条件判断 | 基于变量值的 if/else 多分支逻辑 |
| `code` | 执行代码 | 运行自定义代码（Python/JS），处理变量、解析、计算 |
| `llm` | LLM 调用 | 调用大模型，结果存变量（不直接回复）或直接回复用户 |
| `variable_assign` | 变量赋值 | 直接对变量赋值（无需代码执行） |
| `knowledge_query` | 知识库查询 | RAG 检索，将匹配结果存入变量 |
| `jump` | 跳转 | 跳转到另一个 Flow/Workflow |
| `end` | 结束节点 | 终止对话流 |
| `webhook` | 外部 API 调用 | 调用外部 HTTP 接口 |

---

## 三、AgentStudio 节点 → UIF 反向映射

| AgentStudio 节点类型 | 对应 UIF 类型 | 备注 |
|---|---|---|
| `start` | `start` | 一一对应 |
| `textReply` | `message` | payload 支持 text/buttons/type 完整结构 |
| `captureUserReply` | `input` | 固定存入 `last_user_response` |
| `code` | `code` 或 `variable_assign` | 取决于代码复杂度；简单赋值可转为 `variable_assign` |
| `condition` | `condition` | 支持 AND/OR/Other 多分支 |
| `llmVariableAssignment` | `llm`（存变量模式） | 有 prompt_template，结果不直接展示给用户 |
| `llMReply` | `llm`（回复模式）| 直接向用户发送 LLM 生成内容，支持 RAG |
| `knowledgeAssignment` | `knowledge_query` | RAG 检索，结果存变量 |
| `semanticJudgment` | `intent_router` | Embedding 向量匹配，training phrases 作训练示例 |
| `jump` | `jump` | 按 flow 名称跳转 |
| `block` | _(无对应)_ | AgentStudio 专有 UI 容器，其他平台无需 |

---

## 四、两种意图识别版本的节点链对比

| 对比维度 | V1（知识库版） | V2（语义判断版，当前默认） |
|---|---|---|
| **UIF 节点序列** | `input` → `knowledge_query` → `code` → `condition` | `input` → `intent_router` |
| **AgentStudio 节点链** | `captureUserReply` → `knowledgeAssignment` → `code` → `condition` | `captureUserReply` → `semanticJudgment` |
| **节点数量** | 4 个节点 | 2 个节点 |
| **意图识别原理** | RAG 检索 FAQ 知识库，正则提取意图名 | Embedding 向量相似度匹配训练短语 |
| **依赖外部资源** | 需要 Step3 预先创建知识库 | 无需外部知识库 |
| **优势** | 可动态更新 FAQ 内容 | 部署简单，推理快 |
| **启用参数** | `intent_recognition_version=1` | `intent_recognition_version=2` |

---

## 五、槽位抽取链路详解（#5 展开）

```
Google CX: page.slots = ["card_type", "account_no"]
实体类型:  card_type  → ENUMERATION  → 候选值: ["信用卡", "储蓄卡"]
           account_no → KIND_REGEXP  → 格式: "exactly 6 digits"

生成链路（UIF）:   input → llm → code
生成链路（AgentStudio）:

┌─────────────────────────┐
│  captureUserReply        │  等待用户输入 → last_user_response
└────────────┬────────────┘
             ↓
┌─────────────────────────┐
│  llmVariableAssignment  │  Prompt:
│                         │    ##Parameters to Extract
│                         │    - card_type
│                         │    - account_no
│                         │    ##Hints
│                         │    - card_type: allowed values = 信用卡, 储蓄卡
│                         │    - account_no: format: exactly 6 digits
│                         │  输出 → var_1（JSON字符串）
└────────────┬────────────┘
             ↓
┌─────────────────────────┐
│  code                   │  解析 JSON → 提取 card_type, account_no
│                         │  outputs: ["card_type", "account_no"]
└─────────────────────────┘
```

---

## 六、条件判断类型全览（#12–16 展开）

| 条件类型 | Google CX conditionString 示例 | UIF | AgentStudio 生成方式 |
|---|---|---|---|
| 单一等值 | `$session.params.x = "ok"` | `condition` | condition 节点，1 个 if 分支 |
| 单一不等 | `$session.params.x != "cancel"` | `condition` | comparison_operator: `≠` |
| 大小比较 | `$session.params.count > 3` | `condition` | comparison_operator: `>` / `<` / `≥` / `≤` |
| AND 多条件 | `$session.params.x = "a" AND $session.params.y = "b"` | `condition` | 1 个 condition，多行条件，logical_operator: `and` |
| OR 多条件 | `$session.params.x = "a" OR $session.params.x = "b"` | `condition` | 1 个 condition，多行条件，logical_operator: `or` |
| 混合 AND+OR | `$page.params.status = "FINAL" AND ($session.params.x = "a" OR "b")` | `code` + `condition` | code 节点计算布尔值 condition_result → condition 节点判断 |
| 字面量 true | `true` | `code` + `condition` | code: `if True: return {"condition_result": True}` |
| 字面量 false | `false` | `code` + `condition` | code: `if False: return {"condition_result": False}` |
| 空条件（始终执行） | _(无 comparator)_ | _(直接边)_ | 跳过 condition 节点，直接生成默认连边 |
| disjunction 格式 | `{disjunction: {expressions: [...]}}` | `condition` | 多个 condition_value 合并为一个 condition 节点 |
