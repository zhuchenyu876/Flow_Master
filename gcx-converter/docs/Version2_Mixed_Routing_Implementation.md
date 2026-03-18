# 版本二混合路由处理实现说明

**Author:** senlin.deng  
**Date:** 2026-01-17  
**File:** `step2/converter.py`

## 问题背景

在版本二（Semantic Judgement Version）中，原始实现没有单独的函数处理 page 中 transitionEvents 同时存在 Intent 路由和纯条件路由的情况。

### 原始数据格式示例

```json
{
  "transitionEvents": [
    {
      "triggerIntentId": "32750573-e67c-4a99-b7ef-968092ac9df2",
      "condition": {
        "restriction": {
          "comparator": "EQUALS",
          "lhs": {
            "member": {
              "expressions": [
                {"value": "$session"},
                {"value": "params"},
                {"value": "CCR_PayBillViaOtherChannel"}
              ]
            }
          },
          "rhs": {
            "phrase": {"values": ["Pay"]}
          }
        }
      },
      "transitionEventHandler": {
        "targetPageId": "675fdb37-e19c-4270-8a5f-aa8fb89d0f31"
      }
    },
    {
      "triggerIntentId": "db475a62-3c35-4784-8912-e671464a1745",
      "transitionEventHandler": {
        "targetPageId": "25b66721-3e6b-4ab9-81a1-627c44fe121c"
      }
    },
    {
      "condition": {
        "restriction": {
          "comparator": "EQUALS",
          "lhs": {
            "member": {
              "expressions": [
                {"value": "$session"},
                {"value": "params"},
                {"value": "CCR_SelectedAccount"}
              ]
            }
          },
          "rhs": {"value": "CreditCard"}
        }
      },
      "transitionEventHandler": {
        "targetPageId": "c80b993e-7466-442d-a685-cb58e10a2607"
      }
    }
  ]
}
```

上述数据包含：
- **2个 Intent 路由**：带有 `triggerIntentId` 的路由
- **1个纯条件路由**：只有 `condition`，没有 `triggerIntentId`

## 解决方案

### 核心逻辑

实现了一个新函数 `_generate_semantic_judgment_with_pure_conditions()` 来处理混合路由场景：

1. **如果用户触发了Intent**：走对应的意图分支
2. **如果用户未触发任何Intent**：Semantic Judgement 路由到 Fallback
3. **Fallback 分支连接到纯条件判断节点**：检查纯条件路由

### 执行流程图

```
┌─────────────────────┐
│  Capture User Input │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────────────────┐
│  Semantic Judgment Node         │
│  ┌──────────────────────────┐   │
│  │ Intent 1: PayBill        │───┼──→ LLM → CODE → Condition → Target Page
│  │ Intent 2: YesNo          │───┼──→ LLM → CODE → Condition → Target Page
│  │ Fallback                 │───┼──┐
│  └──────────────────────────┘   │  │
└─────────────────────────────────┘  │
                                     │
                                     ▼
                          ┌────────────────────┐
                          │ Fallback Message   │
                          └─────────┬──────────┘
                                    │
                                    ▼
                          ┌─────────────────────────┐
                          │ Pure Condition Routing  │
                          │ ┌───────────────────┐   │
                          │ │ Condition 1: Var=X│───┼──→ Target Page
                          │ │ Condition 2: Var=Y│───┼──→ Target Page
                          │ │ Other             │───┼──→ Fallback
                          │ └───────────────────┘   │
                          └─────────────────────────┘
```

### 代码修改

#### 1. 在 `_generate_intent_and_condition_nodes()` 中添加检测逻辑

```python
# 版本2：使用语义判断节点进行意图识别
if self.intent_recognition_version == 2 and has_any_intent:
    try:
        # 检查是否同时存在 Intent 路由和纯条件路由
        intent_routes = [t for t in transition_info_list if t['has_intent']]
        pure_condition_routes = [t for t in transition_info_list if not t['has_intent'] and t['has_condition']]
        
        # 如果同时存在，使用专门的处理函数
        if intent_routes and pure_condition_routes:
            result = self._generate_semantic_judgment_with_pure_conditions(
                page, transition_info_list,
                # ... 参数 ...
            )
            return result
        else:
            # 标准的语义判断节点处理
            result = self._generate_semantic_judgment_node(
                page, transition_info_list,
                # ... 参数 ...
            )
            return result
```

#### 2. 新增函数 `_generate_semantic_judgment_with_pure_conditions()`

该函数主要步骤：

1. **分离路由**：将 `transition_info_list` 分为 Intent 路由和纯条件路由
2. **生成 Capture 节点**：收集用户输入
3. **生成 Semantic Judgment 节点**：只包含 Intent 路由
4. **为每个 Intent 生成参数提取链**：如果有参数或条件，复用版本一的参数提取逻辑
5. **生成纯条件判断节点**：处理纯条件路由
6. **生成 Fallback 节点**：显示 Fallback 消息，然后连接到纯条件判断节点
7. **添加 Fallback 分支**：记录 `_next_node` 和 `_fallback_to_pure_condition` 用于边生成

### 关键字段说明

在生成的节点和分支中，添加了以下内部字段（前缀 `_` 表示内部使用）：

- `semantic_node["_has_pure_conditions"]`: 标记有纯条件路由
- `semantic_node["_pure_condition_node"]`: 纯条件判断节点名
- `fallback_branch["_next_node"]`: Fallback 分支的下一个节点（fallback_text）
- `fallback_branch["_fallback_to_pure_condition"]`: Fallback 后连接的纯条件判断节点

这些字段用于 `step6_workflow_generator.py` 中的边生成逻辑。

## 转换结果示例

根据用户提供的示例数据 `output/Page中有Intent路由和纯condition路由拆解.json`，转换后的结构包括：

### 节点列表

1. **Code Node**: VariableAssignment_MM-CCR-27
2. **TextReply Nodes**: Response_MM-CCR-27 (3个)
3. **Capture Node**: Capture User Input
4. **Semantic Judgment Node**: Intent Recognition (包含2个意图)
   - MakingPayments_CCR_PayBillViaOtherChannel
   - Common_YesNo
5. **Fallback TextReply Node**: Fallback Message
6. **LLM Nodes**: Extract Parameters (2个，每个Intent一个)
7. **Code Nodes**: Parse Parameters (2个)
8. **Condition Nodes**: Parameter Routing (2个)
9. **Pure Condition Node**: Condition Routing
10. **SetParameter Code Nodes** 和 **BeforeTransition TextReply Nodes**

### 边连接逻辑

- **Semantic Judgment → Intent 分支**：每个 Intent 连接到对应的 LLM 节点
- **Semantic Judgment → Fallback**：连接到 Fallback Message → Pure Condition Routing
- **Pure Condition Routing**：根据条件路由到不同的目标页面

## 优势

1. **清晰的逻辑分离**：Intent 路由和纯条件路由分别处理，逻辑清晰
2. **Fallback 优雅降级**：未触发 Intent 时，仍可通过纯条件路由进行判断
3. **复用现有代码**：最大程度复用版本一的参数提取逻辑
4. **符合用户期望**：完全按照用户提供的示例数据格式生成

## 后续工作

需要在 `step6_workflow_generator.py` 中更新边生成逻辑，识别并处理以下情况：

- 识别 `_has_pure_conditions` 标记
- 为 Fallback 分支生成两条边：
  1. Semantic Judgment → Fallback TextReply
  2. Fallback TextReply → Pure Condition Routing
- 处理 Pure Condition Routing 的多个条件分支

## 测试建议

使用以下测试数据验证实现：

1. **测试数据**: `input/7/exported_flow_MakingPayments_Fulfillment(router_muli_onec).json`
2. **期望输出**: 类似 `output/Page中有Intent路由和纯condition路由拆解.json` 的结构
3. **验证点**:
   - Semantic Judgment 只包含 Intent 条件
   - Fallback 分支连接到纯条件判断节点
   - 纯条件判断节点包含所有纯条件路由

## 相关文件

- `step2/converter.py`: 实现代码
- `input/7/exported_flow_MakingPayments_Fulfillment(router_muli_onec).json`: 测试数据
- `output/Page中有Intent路由和纯condition路由拆解.json`: 期望输出示例

