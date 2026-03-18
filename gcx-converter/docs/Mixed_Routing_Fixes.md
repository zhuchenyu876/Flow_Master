# 混合路由修正详情

**Author:** senlin.deng  
**Date:** 2026-01-17  
**Issue:** 两个边生成问题

## 问题1: Semantic Judgment 的 Fallback 分支错误连接到 Fallback Message

### 问题描述

在 `step2/converter.py` 的边生成逻辑中，即使设置了 `_has_pure_conditions` 标记，仍然会在某些情况下创建从 Semantic Judgment 的 Fallback 分支到 Fallback Message 的边。

### 问题位置

**文件**: `step2/converter.py` 第4746-4749行

```python
if branch.get('logical_operator') == 'other':
    # Fallback分支 → fallback_text
    if fallback_text_node:
        self._safe_append_edge(edges, semantic_node_name, fallback_text_node, "condition", branch_condition_id, all_nodes)
```

### 修正内容

**修改**: 添加 `_has_pure_conditions` 检查

```python
if branch.get('logical_operator') == 'other':
    # Fallback分支
    if has_pure_conditions:
        # 混合路由：Fallback 已经通过 Step6 直接连接到 Pure Condition Node，不在这里创建边
        logger.debug(f"    🔀 混合路由: 跳过创建 semantic_judgment → fallback_text 边，Fallback 将直接连接到 Pure Condition Node")
        pass
    else:
        # 普通情况：Fallback分支 → fallback_text
        if fallback_text_node:
            self._safe_append_edge(edges, semantic_node_name, fallback_text_node, "condition", branch_condition_id, all_nodes)
```

**效果**:
- ✅ 混合路由：Semantic Judgment Fallback → Pure Condition Node（通过 Step6）
- ✅ 普通路由：Semantic Judgment Fallback → Fallback Message（通过 Step2）

## 问题2: Pure Condition Routing 的 Other 分支有额外边

### 问题描述

Pure Condition Routing 的 Other 分支应该**只能连接到 Fallback Message**，不能再有其他的边。但通用的 condition 节点边生成逻辑会为有 `target_flow_id` 或 `target_page_id` 的分支创建额外的边。

### 问题位置

**文件**: `step2/converter.py` 第7083-7094行

```python
for node in all_nodes:
    if node.get("type") == "condition":
        condition_name = node.get("name")
        if_else_conditions = node.get("if_else_conditions", [])
        
        for branch in if_else_conditions:
            target_flow_id = branch.get("target_flow_id")
            target_page_id = branch.get("target_page_id")
            condition_id = branch.get("condition_id")
            
            # 只有当有 targetFlowId 且无 targetPageId 时才需要连接到 jump 节点
            if target_flow_id and not target_page_id:
                # ... 创建边到 jump 节点
```

### 修正内容

**修改**: 跳过 Pure Condition Routing 的 Other 分支（有 `_next_node` 的分支）

```python
for node in all_nodes:
    if node.get("type") == "condition":
        condition_name = node.get("name")
        if_else_conditions = node.get("if_else_conditions", [])

        # writed by senlin.deng 2026-01-17
        # 检查是否是 Pure Condition Routing 节点（混合路由中的纯条件判断节点）
        is_pure_condition_routing = node.get("title") == "Condition Routing" and any(
            branch.get("_next_node") for branch in if_else_conditions
        )

        for branch in if_else_conditions:
            target_flow_id = branch.get("target_flow_id")
            target_page_id = branch.get("target_page_id")
            condition_id = branch.get("condition_id")

            # writed by senlin.deng 2026-01-17
            # Pure Condition Routing 的 Other 分支（有 _next_node）只连接到 Fallback Message，不走通用逻辑
            if is_pure_condition_routing and branch.get("_next_node"):
                logger.debug(f"    🔀 跳过 Pure Condition Routing 的 Other 分支通用边生成: {condition_name} [{branch.get('condition_name')}] → {branch.get('_next_node')}")
                continue

            # 只有当有 targetFlowId 且无 targetPageId 时才需要连接到 jump 节点
            if target_flow_id and not target_page_id:
                # ... 创建边到 jump 节点
```

**效果**:
- ✅ Pure Condition Routing 的 Other 分支：只连接到 Fallback Message（通过 `_next_node`）
- ✅ 其他分支：正常走通用逻辑（连接到目标页面或 jump 节点）

## 修正后的完整流程

```
用户输入
    ↓
Capture User Input
    ↓
Semantic Judgment (Intent Recognition)
    ├─→ Intent 1 → 参数提取 → 目标
    ├─→ Intent 2 → 参数提取 → 目标
    └─→ Fallback → Pure Condition Routing
                      ├─→ 条件1 → 目标页面1
                      ├─→ 条件2 → 目标页面2
                      └─→ Other → Fallback Message ✅
```

## 边连接验证

### 修正前（错误）

- ❌ Semantic Judgment Fallback → Fallback Message（额外边）
- ❌ Pure Condition Routing Other → 其他节点（额外边）

### 修正后（正确）

- ✅ Semantic Judgment Fallback → Pure Condition Routing（Step6）
- ✅ Pure Condition Routing Other → Fallback Message（`_next_node`）
- ✅ 没有其他额外边

## 日志输出

### 问题1修正日志

```
🔀 混合路由: 跳过创建 semantic_judgment → fallback_text 边，Fallback 将直接连接到 Pure Condition Node
```

### 问题2修正日志

```
🔀 跳过 Pure Condition Routing 的 Other 分支通用边生成: pure_condition_routing_xxx [Other] → fallback_text_xxx
```

## 验证方法

### Step2 输出检查

运行转换后，检查 `edges_config.json`：

**不应包含**:
```json
{
  "source_node": "semantic_judgment_xxx",
  "target_node": "fallback_text_xxx",
  "connection_type": "condition"
}
```

**不应包含** Pure Condition Routing 的 Other 分支到其他节点的边（除了 Fallback Message）。

### Step6 输出检查

检查 `generated_workflow.json` 中的边：

**应该包含**:
```json
{
  "source": "semantic_judgment_block_id",
  "target": "pure_condition_routing_block_id",
  "sourceHandle": "<fallback_handle_id>"
}
```

**应该包含**:
```json
{
  "source": "pure_condition_routing_block_id",
  "target": "fallback_message_block_id",
  "sourceHandle": "<other_handle_id>"
}
```

## 相关文档

- [Version2_Mixed_Routing_Implementation.md](./Version2_Mixed_Routing_Implementation.md) - 初始实现
- [Mixed_Routing_Fix_Direct_Connection.md](./Mixed_Routing_Fix_Direct_Connection.md) - 第一次修正
- [Mixed_Routing_Complete_Flow.md](./Mixed_Routing_Complete_Flow.md) - 完整流程

