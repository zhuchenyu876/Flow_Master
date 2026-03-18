# 混合路由完整流程说明

**Author:** senlin.deng  
**Date:** 2026-01-17  
**Feature:** 版本二 Intent + 纯条件路由混合处理（完整版）

## 完整流程图

```
用户输入
    ↓
Capture User Input
    ↓
Semantic Judgment (Intent Recognition)
    ├─→ Intent 1 匹配 → 参数提取 → 条件判断 → 目标页面
    ├─→ Intent 2 匹配 → 参数提取 → 条件判断 → 目标页面
    └─→ Fallback (未匹配任何Intent) → Pure Condition Routing
                                          ├─→ 条件1满足 → 目标页面1
                                          ├─→ 条件2满足 → 目标页面2
                                          └─→ Other → Fallback Message
```

## 节点和边连接详解

### 1. Semantic Judgment → Pure Condition Routing

**连接类型**: Condition Edge  
**触发条件**: 用户输入未匹配任何 Intent（Fallback 分支）

```json
{
  "source": "semantic_judgment_block_id",
  "target": "pure_condition_routing_block_id",
  "sourceHandle": "<fallback_condition_handle_id>"
}
```

### 2. Pure Condition Routing → Fallback Message

**连接类型**: Condition Edge  
**触发条件**: 所有纯条件都不满足（Other 分支）

```json
{
  "source": "pure_condition_routing_block_id",
  "target": "fallback_message_block_id",
  "sourceHandle": "<other_condition_handle_id>"
}
```

### 3. Pure Condition Routing → 目标页面

**连接类型**: Condition Edge  
**触发条件**: 纯条件满足

```json
{
  "source": "pure_condition_routing_block_id",
  "target": "target_page_block_id",
  "sourceHandle": "<condition_handle_id>"
}
```

## 实现细节

### Step2 实现 (`step2/converter.py`)

#### 1. Semantic Judgment 的 Fallback 分支

**位置**: `_generate_semantic_judgment_with_pure_conditions()` 第1114-1126行

```python
# Fallback 分支直接连接到 Pure Condition Routing
fallback_branch = {
    "condition_id": default_condition_id,
    "condition_name": "Fallback",
    "logical_operator": "other",
    "conditions": [],
    "condition_action": [],
    "from_semantic_node": semantic_node_name,
    "_direct_target": pure_condition_node_name  # ✅ 直接连接
}
```

#### 2. Pure Condition Routing 的生成

**位置**: 第1073-1112行

先生成 Fallback Message 节点，然后添加 Other 分支指向它：

```python
# 5. 先生成 Fallback Message 节点
fallback_node_name = gen_unique_node_name('fallback_text', page_id)
fallback_node = {
    "type": "textReply",
    "name": fallback_node_name,
    "title": "Fallback Message",
    # ...
}
nodes.append(fallback_node)

# 5.1 添加 Other 分支（连接到 Fallback Message）
pure_condition_branches.append({
    "condition_id": pure_fallback_condition_id,
    "condition_name": "Other",
    "logical_operator": "other",
    "conditions": [],
    "condition_action": [],
    "_next_node": fallback_node_name  # ✅ 连接到 Fallback Message
})

# 5.2 生成 Pure Condition Routing 节点
pure_condition_node = {
    "type": "condition",
    "name": pure_condition_node_name,
    "title": "Condition Routing",
    "if_else_conditions": pure_condition_branches
}
```

### Step6 实现 (`step6_workflow_generator.py`)

#### 1. 处理 Condition 节点的 _next_node 字段

**位置**: 第1279-1301行

遍历所有 condition 节点，检查每个分支是否有 `_next_node` 字段：

```python
# 处理 condition 节点的 _next_node 字段
for node_config in node_configs:
    node_type = node_config.get("type")
    
    if node_type == "condition" and node_name in node_name_map:
        if_else_conditions = node_config.get("if_else_conditions", [])
        
        for condition_branch in if_else_conditions:
            next_node_name = condition_branch.get("_next_node")
            if next_node_name and next_node_name in node_name_map:
                condition_id = condition_branch.get("condition_id")
                
                # 生成 condition edge
                edge = edge_manager._create_condition_edge(
                    condition_node_info, next_node_info, condition_id
                )
                edges.append(edge)
```

#### 2. 处理 Semantic Judgment 的 Fallback 分支

**位置**: 第1303-1330行

直接连接 Semantic Judgment → Pure Condition Routing：

```python
# 处理 semanticJudgment 节点的混合路由
for semantic_node_name, mixed_routing_info in edge_manager.semantic_judgment_with_pure_conditions.items():
    fallback_direct_target = mixed_routing_info["fallback_direct_target"]
    
    # 找到 Fallback 的 condition_id
    fallback_condition_id = # ... 从配置中查找
    
    # 生成边：SemanticJudgment → Pure Condition Node
    fallback_edge = edge_manager._create_condition_edge(
        semantic_node_info, pure_condition_node_info, fallback_condition_id
    )
    edges.append(fallback_edge)
```

## 执行场景

### 场景1: 用户触发 Intent

```
用户输入: "Pay bill via other channel"
    ↓
Capture User Input
    ↓
Semantic Judgment → 匹配 Intent "MakingPayments_CCR_PayBillViaOtherChannel"
    ↓
LLM (提取参数)
    ↓
CODE (解析参数)
    ↓
Condition (检查参数条件)
    ↓
目标页面
```

### 场景2: 未触发 Intent，但满足纯条件

```
用户输入: "Something else"
    ↓
Capture User Input
    ↓
Semantic Judgment → 未匹配任何 Intent (Fallback)
    ↓
Pure Condition Routing → 检查 CCR_SelectedAccount
    ↓
条件满足 (CCR_SelectedAccount = "CreditCard")
    ↓
目标页面
```

### 场景3: 未触发 Intent，且不满足任何纯条件

```
用户输入: "Something else"
    ↓
Capture User Input
    ↓
Semantic Judgment → 未匹配任何 Intent (Fallback)
    ↓
Pure Condition Routing → 检查所有条件
    ↓
所有条件都不满足 (Other 分支)
    ↓
Fallback Message ("I didn't get that. Can you repeat?")
```

## 关键字段说明

### Semantic Judgment 节点

```json
{
  "type": "semanticJudgment",
  "name": "semantic_judgment_xxx",
  "_has_pure_conditions": true,
  "_pure_condition_node": "pure_condition_routing_xxx",
  "_internal_branches": [
    {
      "condition_id": "uuid-fallback",
      "condition_name": "Fallback",
      "_direct_target": "pure_condition_routing_xxx"  // ✅ 直接目标
    }
  ]
}
```

### Pure Condition Routing 节点

```json
{
  "type": "condition",
  "name": "pure_condition_routing_xxx",
  "title": "Condition Routing",
  "if_else_conditions": [
    {
      "condition_id": "uuid-1",
      "condition_name": "Pure Condition Route 1",
      "conditions": [{"condition_variable": "ccr_selectedaccount", ...}],
      "target_page_id": "..."
    },
    {
      "condition_id": "uuid-other",
      "condition_name": "Other",
      "logical_operator": "other",
      "_next_node": "fallback_text_xxx"  // ✅ 连接到 Fallback Message
    }
  ]
}
```

### Fallback Message 节点

```json
{
  "type": "textReply",
  "name": "fallback_text_xxx",
  "title": "Fallback Message",
  "plain_text": [{
    "text": "{\"text\": \"I didn't get that. Can you repeat?\", \"type\": \"message\"}"
  }]
}
```

## 验证清单

### Step2 输出验证

- [x] Semantic Judgment 节点有 `_has_pure_conditions: true`
- [x] Semantic Judgment 节点有 `_direct_target` 指向 Pure Condition Routing
- [x] Pure Condition Routing 节点有 Other 分支
- [x] Other 分支有 `_next_node` 指向 Fallback Message
- [x] Fallback Message 节点已生成

### Step6 输出验证

- [x] 存在边：`Semantic Judgment → Pure Condition Routing`
- [x] 存在边：`Pure Condition Routing [Other] → Fallback Message`
- [x] 每个纯条件分支都有边连接到目标页面
- [x] 所有边的 sourceHandle 和 targetHandle 都正确

### 功能验证

- [x] 场景1：用户触发 Intent → 走 Intent 分支
- [x] 场景2：未触发 Intent，满足纯条件 → 走纯条件分支
- [x] 场景3：未触发 Intent，不满足纯条件 → 显示 Fallback Message

## 日志输出示例

```
Step 2 - Page Processing:
  🔀 检测到混合路由: 2 个Intent路由, 1 个纯条件路由
  ✅ 生成混合路由: SemanticJudgment(2个意图) + 纯条件判断(1个条件)

Step 6 - Edge Generation:
  🔀 注册混合路由: semantic_judgment_xxx -> Fallback直接连接: pure_condition_routing_xxx
  ✅ 生成 Condition 分支边: pure_condition_routing_xxx [Other] → fallback_text_xxx
  ✅ 生成 Fallback 边: semantic_judgment_xxx → pure_condition_routing_xxx (直接连接)
```

## 与参考输出对比

参考文件：`output/Page中有Intent路由和纯condition路由拆解.json`

### 边连接对比

| 边 | 参考输出 | 当前实现 |
|----|----------|----------|
| Semantic Judgment → Pure Condition | ✅ | ✅ |
| Pure Condition [Other] → Fallback Message | ✅ | ✅ |
| Pure Condition [条件1] → 目标页面 | ✅ | ✅ |
| Intent 分支 → 参数提取 | ✅ | ✅ |

## 相关文档

- [Version2_Mixed_Routing_Implementation.md](./Version2_Mixed_Routing_Implementation.md) - 初始实现
- [Mixed_Routing_Fix_Direct_Connection.md](./Mixed_Routing_Fix_Direct_Connection.md) - 修正 Fallback 直接连接
- [Mixed_Routing_Testing_Guide.md](./Mixed_Routing_Testing_Guide.md) - 测试指南

## 总结

混合路由的完整实现包括三个关键连接：

1. **Semantic Judgment → Pure Condition Routing**: 用户未触发 Intent 时的 Fallback 路由
2. **Pure Condition Routing → 目标页面**: 纯条件满足时的路由
3. **Pure Condition Routing → Fallback Message**: 所有条件都不满足时的最终 Fallback

这种设计确保了：
- Intent 优先级最高（先判断 Intent）
- 纯条件作为 Fallback 的补充判断
- Fallback Message 作为最终的错误处理

整个流程清晰、优雅，符合对话流的最佳实践。

