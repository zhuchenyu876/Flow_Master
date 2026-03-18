# 混合路由修正：Fallback 直接连接到纯条件判断

**Author:** senlin.deng  
**Date:** 2026-01-17  
**Issue:** Semantic Judgment 的 Fallback 分支错误地连接到 Fallback Message，而不是直接连接到 Pure Condition Node

## 问题描述

### 原始错误流程

```
Semantic Judgment (Intent Recognition)
    ├─→ Intent 1 → ...
    ├─→ Intent 2 → ...
    └─→ Fallback → ❌ Fallback Message → Condition Routing
```

**问题**: Fallback 分支先连接到 Fallback Message 节点，再连接到 Condition Routing，这是错误的。

### 正确流程（根据参考输出）

```
Semantic Judgment (Intent Recognition)
    ├─→ Intent 1 → ...
    ├─→ Intent 2 → ...
    └─→ Fallback → ✅ Condition Routing (直接连接)
```

**正确做法**: Fallback 分支应该直接连接到 Condition Routing（纯条件判断节点）。

## 修正内容

### 1. Step2 修正 (`step2/converter.py`)

#### 修正位置：`_generate_semantic_judgment_with_pure_conditions()` 函数

**第1113-1124行 - 修改 Fallback 分支配置**

修正前：
```python
fallback_branch = {
    "condition_id": default_condition_id,
    "condition_name": "Fallback",
    "logical_operator": "other",
    "conditions": [],
    "condition_action": [],
    "from_semantic_node": semantic_node_name,
    "_next_node": fallback_node_name,  # ❌ 错误：先到 fallback_text
    "_fallback_to_pure_condition": pure_condition_node_name
}
```

修正后：
```python
fallback_branch = {
    "condition_id": default_condition_id,
    "condition_name": "Fallback",
    "logical_operator": "other",
    "conditions": [],
    "condition_action": [],
    "from_semantic_node": semantic_node_name,
    "_direct_target": pure_condition_node_name  # ✅ 正确：直接连接到纯条件判断
}
```

**关键变化**:
- 删除 `_next_node` 字段（它指向 Fallback Message）
- 删除 `_fallback_to_pure_condition` 字段
- 新增 `_direct_target` 字段，直接指向 `pure_condition_node_name`

#### 第1584-1599行 - semantic_branches_for_edges

修正后的代码会自动使用新的 `fallback_branch`：

```python
semantic_branches_for_edges.append({
    "condition_id": default_condition_id,
    "condition_name": "Fallback",
    "from_semantic_node": semantic_node_name,
    "_direct_target": pure_condition_node_name  # ✅ 直接连接
})
```

### 2. Step6 修正 (`step6_workflow_generator.py`)

#### 修正位置1：节点处理逻辑（第1218-1238行）

修正前：
```python
# 从 _internal_branches 中查找 Fallback 分支的 _next_node
for branch in internal_branches:
    if branch.get("condition_name") == "Fallback" and "_next_node" in branch:
        fallback_next_node = branch.get("_next_node")  # ❌ 查找 _next_node
        break

if has_pure_conditions and fallback_next_node and pure_condition_node:
    edge_manager.register_semantic_judgment_with_pure_conditions(
        node_name, fallback_next_node, pure_condition_node
    )
```

修正后：
```python
# 从 _internal_branches 中查找 Fallback 分支的 _direct_target
for branch in internal_branches:
    if branch.get("condition_name") == "Fallback" and "_direct_target" in branch:
        fallback_direct_target = branch.get("_direct_target")  # ✅ 查找 _direct_target
        break

if has_pure_conditions and fallback_direct_target and pure_condition_node:
    edge_manager.register_semantic_judgment_with_pure_conditions(
        node_name, fallback_direct_target, pure_condition_node
    )
```

#### 修正位置2：EdgeManager 注册方法（第215-227行）

修正前：
```python
def register_semantic_judgment_with_pure_conditions(self, node_name, fallback_next_node, pure_condition_node):
    self.semantic_judgment_with_pure_conditions[node_name] = {
        "fallback_next_node": fallback_next_node,
        "pure_condition_node": pure_condition_node
    }
```

修正后：
```python
def register_semantic_judgment_with_pure_conditions(self, node_name, fallback_direct_target, pure_condition_node):
    self.semantic_judgment_with_pure_conditions[node_name] = {
        "fallback_direct_target": fallback_direct_target,  # ✅ 直接目标
        "pure_condition_node": pure_condition_node
    }
```

#### 修正位置3：边生成逻辑（第1279-1320行）

修正前（生成两条边）：
```python
# 1. SemanticJudgment → Fallback Text
fallback_edge = edge_manager._create_condition_edge(
    semantic_node_info, fallback_text_node_info, fallback_condition_id
)
edges.append(fallback_edge)

# 2. Fallback Text → Pure Condition Node
pure_condition_edge = edge_manager._create_default_edge(
    fallback_text_node_info, pure_condition_node_info
)
edges.append(pure_condition_edge)
```

修正后（只生成一条边）：
```python
# 直接连接：SemanticJudgment → Pure Condition Node
if fallback_condition_id:
    fallback_edge = edge_manager._create_condition_edge(
        semantic_node_info, pure_condition_node_info, fallback_condition_id
    )
    edges.append(fallback_edge)
    logger.debug(f"  ✅ 生成 Fallback 边: {semantic_node_name} → {pure_condition_node_name} (直接连接)")
```

## 边类型说明

### Condition Edge（用于 Fallback 分支）

```json
{
  "id": "vueflow__edge-semantic_judgment_xxx<fallback_handle>-pure_condition_xxx<target_handle>",
  "type": "custom",
  "source": "semantic_judgment_block_id",
  "target": "pure_condition_block_id",
  "sourceHandle": "<fallback_condition_handle_id>",
  "targetHandle": "<pure_condition_target_handle>"
}
```

**关键特性**:
- 使用 `sourceHandle` 指定 Fallback 条件的句柄ID
- 直接连接到 Pure Condition Routing 节点
- 不经过 Fallback Message 节点

## Fallback Message 节点的用途

Fallback Message 节点仍然会生成，但它不在主流程中：

```python
# 5. 生成 Fallback 文本节点（用于其他错误情况，不在主流程中）
fallback_node = {
    "type": "textReply",
    "name": fallback_node_name,
    "title": "Fallback Message",
    "plain_text": [{
        "text": fallback_message,
        "id": fallback_node_name
    }]
}
nodes.append(fallback_node)
```

**用途**:
- 可能用于 Pure Condition Routing 的 "Other" 分支
- 可能用于其他错误处理场景
- 不是 Semantic Judgment Fallback 的直接下游节点

## 流程对比

### 修正前（错误）

```
用户输入: "Something else"
    ↓
Capture User Input
    ↓
Semantic Judgment (未匹配任何 Intent → Fallback)
    ↓
❌ Fallback Message ("I didn't get that...")
    ↓
Pure Condition Routing (检查 CCR_SelectedAccount)
    ↓
目标页面
```

### 修正后（正确）

```
用户输入: "Something else"
    ↓
Capture User Input
    ↓
Semantic Judgment (未匹配任何 Intent → Fallback)
    ↓
✅ Pure Condition Routing (直接检查 CCR_SelectedAccount)
    ├─→ 条件满足 → 目标页面
    └─→ 条件不满足 (Other) → 可能到 Fallback Message
```

## 验证方法

### 检查 Step2 输出 (nodes_config.json)

查找 semanticJudgment 节点的 `_internal_branches`：

```json
{
  "type": "semanticJudgment",
  "_internal_branches": [
    {
      "condition_id": "uuid-fallback",
      "condition_name": "Fallback",
      "_direct_target": "pure_condition_routing_xxx"  // ✅ 应该有这个字段
    }
  ]
}
```

**验证点**:
- ✅ 有 `_direct_target` 字段
- ❌ 没有 `_next_node` 字段
- ❌ 没有 `_fallback_to_pure_condition` 字段

### 检查 Step6 输出 (generated_workflow.json)

查找从 Semantic Judgment 到 Pure Condition Routing 的边：

```json
{
  "id": "vueflow__edge-<semantic_judgment_id><fallback_handle>-<pure_condition_id><target_handle>",
  "source": "<semantic_judgment_block_id>",
  "target": "<pure_condition_block_id>",
  "sourceHandle": "<fallback_condition_handle>"
}
```

**验证点**:
- ✅ source 是 Semantic Judgment 的 block ID
- ✅ target 是 Pure Condition Routing 的 block ID
- ✅ sourceHandle 对应 Fallback 条件的 handle
- ❌ 不应该有从 Semantic Judgment 到 Fallback Message 的边（使用 Fallback sourceHandle）

### 检查日志输出

应该看到：

```
Step 6 - Edge Generation:
  🔀 注册混合路由: semantic_judgment_xxx -> Fallback直接连接: pure_condition_routing_xxx
  ✅ 生成 Fallback 边: semantic_judgment_xxx → pure_condition_routing_xxx (直接连接)
```

**不应该看到**：
```
❌ 生成 Fallback 边: semantic_judgment_xxx → fallback_text_xxx
❌ 生成边: fallback_text_xxx → pure_condition_routing_xxx
```

## 相关文件

- `step2/converter.py`: Step2 修正（Fallback 分支配置）
- `step6_workflow_generator.py`: Step6 修正（边生成逻辑）
- `output/Page中有Intent路由和纯condition路由拆解.json`: 参考输出（正确的边连接）

## 修正总结

| 方面 | 修正前 | 修正后 |
|------|--------|--------|
| Fallback 分支字段 | `_next_node: fallback_text` | `_direct_target: pure_condition_routing` |
| 边连接数量 | 2条（Semantic → Fallback Text → Pure Condition） | 1条（Semantic → Pure Condition） |
| Fallback Message | 在主流程中 | 不在主流程中（用于其他错误处理） |
| 执行流程 | 先显示消息，再判断条件 | 直接判断条件 |

这个修正确保了混合路由的行为与参考输出一致，Semantic Judgment 的 Fallback 分支直接连接到纯条件判断节点。

