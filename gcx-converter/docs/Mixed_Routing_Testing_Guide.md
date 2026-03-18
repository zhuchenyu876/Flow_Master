# 混合路由功能测试指南

**Date:** 2026-01-17  
**Feature:** 版本二 Intent + 纯条件路由混合处理

## 测试目标

验证版本二（Semantic Judgement Version）能够正确处理 page 中 transitionEvents 同时存在 Intent 路由和纯条件路由的情况。

## 测试数据

### 测试文件
- **输入文件**: `input/7/exported_flow_MakingPayments_Fulfillment(router_muli_onec).json`
- **参考输出**: `output/Page中有Intent路由和纯condition路由拆解.json`

### 测试场景特征

原始 flow 包含混合路由：
- **2个 Intent 路由**:
  1. `MakingPayments_CCR_PayBillViaOtherChannel` (带条件)
  2. `Common_YesNo`
- **1个纯条件路由**:
  - 条件: `$session.params.CCR_SelectedAccount = "CreditCard"`

## 测试步骤

### 1. 运行转换流程

```bash
# 假设使用 run_all_steps_server.py 或直接调用 converter
python run_all_steps_server.py \
  --input input/7/exported_flow_MakingPayments_Fulfillment(router_muli_onec).json \
  --language en \
  --intent_recognition_version 2
```

### 2. 检查 Step2 输出

检查生成的 `nodes_config.json` 中是否包含以下节点：

#### 2.1 Semantic Judgment 节点

```json
{
  "type": "semanticJudgment",
  "name": "semantic_judgment_xxx",
  "_has_pure_conditions": true,
  "_pure_condition_node": "pure_condition_routing_xxx",
  "_internal_branches": [
    {
      "condition_id": "uuid-1",
      "condition_name": "Intent_MakingPayments_CCR_PayBillViaOtherChannel",
      // ...
    },
    {
      "condition_id": "uuid-2",
      "condition_name": "Intent_Common_YesNo",
      // ...
    },
    {
      "condition_id": "uuid-fallback",
      "condition_name": "Fallback",
      "_next_node": "fallback_text_xxx",
      "_fallback_to_pure_condition": "pure_condition_routing_xxx"
    }
  ],
  "config": {
    "semantic_conditions": [
      {
        "condition_id": "uuid-1",
        "name": "MakingPayments_CCR_PayBillViaOtherChannel",
        "positive_examples": [...]
      },
      {
        "condition_id": "uuid-2",
        "name": "Common_YesNo",
        "positive_examples": [...]
      }
    ],
    "default_condition": {
      "condition_id": "uuid-fallback",
      "name": "Fallback_Intent"
    }
  }
}
```

#### 2.2 Fallback Text 节点

```json
{
  "type": "textReply",
  "name": "fallback_text_xxx",
  "title": "Fallback Message",
  "plain_text": [
    {
      "text": "{\"text\": \"I didn't get that. Can you repeat?\", \"type\": \"message\"}",
      "id": "..."
    }
  ]
}
```

#### 2.3 Pure Condition Routing 节点

```json
{
  "type": "condition",
  "name": "pure_condition_routing_xxx",
  "title": "Condition Routing",
  "if_else_conditions": [
    {
      "condition_id": "uuid-pure-1",
      "condition_name": "Pure Condition Route 1",
      "logical_operator": "and",
      "conditions": [
        {
          "condition_type": "variable",
          "comparison_operator": "=",
          "condition_value": "CreditCard",
          "condition_variable": "ccr_selectedaccount"
        }
      ],
      "target_page_id": "c80b993e-7466-442d-a685-cb58e10a2607"
    },
    {
      "condition_id": "uuid-other",
      "condition_name": "Other",
      "logical_operator": "other",
      "conditions": []
    }
  ]
}
```

### 3. 检查 Step6 输出

检查生成的 `generated_workflow.json` 中的边连接：

#### 3.1 Semantic Judgment 的 Fallback 边

查找从 Semantic Judgment 节点到 Fallback Text 节点的边：

```json
{
  "id": "vueflow__edge-semantic_judgment_xxx<fallback_handle_id>-fallback_text_xxx<target_handle>",
  "type": "custom",
  "source": "semantic_judgment_xxx",
  "target": "fallback_text_xxx",
  "sourceHandle": "<fallback_condition_handle_id>",
  "targetHandle": "<fallback_text_target_handle>"
}
```

**验证点**:
- `source` 是 Semantic Judgment 节点的 block ID
- `target` 是 Fallback Text 节点的 block ID
- `sourceHandle` 对应 Fallback 条件的 handle ID

#### 3.2 Fallback Text 到 Pure Condition 的边

查找从 Fallback Text 到 Pure Condition Routing 的边：

```json
{
  "id": "vueflow__edge-fallback_text_xxx<source_handle>-pure_condition_routing_xxx<target_handle>",
  "type": "custom",
  "source": "fallback_text_xxx",
  "target": "pure_condition_routing_xxx",
  "sourceHandle": "<fallback_text_source_handle>",
  "targetHandle": "<pure_condition_target_handle>"
}
```

**验证点**:
- `source` 是 Fallback Text 节点的 block ID
- `target` 是 Pure Condition Routing 节点的 block ID

#### 3.3 Pure Condition Routing 的分支边

查找从 Pure Condition Routing 到目标页面的边（应该在 edge_config.json 中定义）：

```json
{
  "id": "vueflow__edge-pure_condition_routing_xxx<condition_handle>-target_page_xxx<target_handle>",
  "type": "custom",
  "source": "pure_condition_routing_xxx",
  "target": "target_page_xxx",
  "sourceHandle": "<pure_condition_handle_id>",
  "targetHandle": "<target_page_handle>"
}
```

### 4. 检查日志输出

运行转换时，应该看到以下日志：

```
Step 2 - Page Processing:
  🔀 检测到混合路由: 2 个Intent路由, 1 个纯条件路由
  ✅ 生成混合路由: SemanticJudgment(2个意图) + 纯条件判断(1个条件)

Step 6 - Edge Generation:
  🔀 注册混合路由: semantic_judgment_xxx -> Fallback: fallback_text_xxx -> Pure Condition: pure_condition_routing_xxx
  ✅ 生成 Fallback 边: semantic_judgment_xxx → fallback_text_xxx
  ✅ 生成边: fallback_text_xxx → pure_condition_routing_xxx
```

## 验证清单

### Step2 输出验证

- [ ] `semantic_judgment` 节点包含 `_has_pure_conditions: true`
- [ ] `semantic_judgment` 节点包含 `_pure_condition_node` 字段
- [ ] `_internal_branches` 包含 Fallback 分支，且有 `_next_node` 字段
- [ ] 生成了 `fallback_text` 节点
- [ ] 生成了 `pure_condition_routing` 节点
- [ ] `pure_condition_routing` 节点包含纯条件的分支信息

### Step6 输出验证

- [ ] 存在边：`semantic_judgment` → `fallback_text`
- [ ] 存在边：`fallback_text` → `pure_condition_routing`
- [ ] 存在边：`pure_condition_routing` → 目标页面（对于每个满足的条件）
- [ ] 存在边：`semantic_judgment` → Intent 分支的下一个节点（LLM 或目标页面）
- [ ] 所有边的 `sourceHandle` 和 `targetHandle` 都正确

### 功能验证

- [ ] Intent 路由正常工作（用户触发 Intent 时走对应分支）
- [ ] Fallback 路由正常工作（用户未触发任何 Intent 时走 Fallback）
- [ ] 纯条件判断正常工作（Fallback 后根据条件路由到不同页面）

## 预期行为

### 场景1: 用户触发 Intent

```
用户输入: "Pay bill via other channel"
  ↓
Capture User Input
  ↓
Semantic Judgment (识别为 MakingPayments_CCR_PayBillViaOtherChannel)
  ↓
LLM (提取参数)
  ↓
CODE (解析参数)
  ↓
Condition (检查参数条件)
  ↓
目标页面 (根据条件路由)
```

### 场景2: 用户未触发 Intent，但满足纯条件

```
用户输入: "Something else"
  ↓
Capture User Input
  ↓
Semantic Judgment (未匹配任何 Intent → Fallback)
  ↓
Fallback Message ("I didn't get that...")
  ↓
Pure Condition Routing (检查 CCR_SelectedAccount)
  ↓
目标页面 (如果 CCR_SelectedAccount = "CreditCard")
```

### 场景3: 用户未触发 Intent，且不满足纯条件

```
用户输入: "Something else"
  ↓
Capture User Input
  ↓
Semantic Judgment (未匹配任何 Intent → Fallback)
  ↓
Fallback Message ("I didn't get that...")
  ↓
Pure Condition Routing (检查失败 → Other 分支)
  ↓
Fallback 页面或其他默认处理
```

## 常见问题

### Q1: Fallback 边没有生成

**检查**:
- Step2 输出中 `semantic_judgment` 节点是否有 `_has_pure_conditions: true`
- `_internal_branches` 中是否有 Fallback 分支，且包含 `_next_node` 字段
- `_next_node` 指向的节点名称是否在 `nodes_config.json` 中存在

### Q2: Pure Condition Routing 的边没有生成

**检查**:
- `edge_config.json` 中是否定义了 Pure Condition Routing 的分支边
- Pure Condition Node 的名称是否与 `_pure_condition_node` 字段一致

### Q3: 边的 sourceHandle 不正确

**检查**:
- Step6 是否正确映射了 `condition_id` 到内部 handle ID
- `_create_condition_edge` 方法是否正确使用了 `condition_mappings`

## 调试技巧

1. **启用详细日志**:
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   ```

2. **检查中间文件**:
   ```bash
   ls -la output/step2/
   ls -la output/step6/
   ```

3. **比对参考输出**:
   ```bash
   diff output/generated_workflow.json output/Page中有Intent路由和纯condition路由拆解.json
   ```

4. **可视化检查**:
   - 将生成的 workflow JSON 导入到对话流平台
   - 在可视化编辑器中检查节点和边的连接
   - 测试不同的用户输入场景

## 成功标准

所有以下条件都满足时，表示实现成功：

✅ Step2 正确生成混合路由的节点配置  
✅ Step6 正确生成 Fallback 到纯条件的边  
✅ 导入到平台后，可视化显示正确  
✅ 测试所有场景，行为符合预期  
✅ 日志输出清晰，无错误或警告  

## 相关文档

- [Version2_Mixed_Routing_Implementation.md](./Version2_Mixed_Routing_Implementation.md) - Step2 实现说明
- [Step6_Mixed_Routing_Edge_Generation.md](./Step6_Mixed_Routing_Edge_Generation.md) - Step6 实现说明

