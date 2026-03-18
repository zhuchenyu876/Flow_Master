# Step6 混合路由边生成实现说明

**Author:** senlin.deng  
**Date:** 2026-01-17  
**File:** `step6_workflow_generator.py`

## 实现目标

在 step6 中实现对版本二混合路由（Intent + 纯条件路由）的边生成逻辑，确保：

1. 识别 `_has_pure_conditions` 标记
2. 为 Fallback 分支生成：Semantic Judgment → Fallback Text → Pure Condition Node
3. 处理 Pure Condition Node 的多个条件分支

## 代码修改

### 1. EdgeManager 类扩展

**位置**: `step6_workflow_generator.py` 第 203-227 行

添加了对混合路由的 semanticJudgment 节点的支持：

```python
class EdgeManager:
    def __init__(self):
        self.edges = []
        self.condition_mappings = {}
        self.semantic_judgment_with_pure_conditions = {}  # 新增：存储混合路由信息
    
    def register_semantic_judgment_with_pure_conditions(self, node_name, fallback_next_node, pure_condition_node):
        """
        注册有纯条件路由的 semanticJudgment 节点
        
        Args:
            node_name: semanticJudgment 节点名称
            fallback_next_node: Fallback 分支的下一个节点（通常是 Fallback Message 节点）
            pure_condition_node: 纯条件判断节点名称
        """
        self.semantic_judgment_with_pure_conditions[node_name] = {
            "fallback_next_node": fallback_next_node,
            "pure_condition_node": pure_condition_node
        }
```

### 2. semanticJudgment 节点处理逻辑

**位置**: `step6_workflow_generator.py` 第 1206-1240 行

在处理 semanticJudgment 节点时，检查并注册混合路由信息：

```python
elif node_type == "semanticJudgment":
    # ... 创建节点 ...
    
    # 检查是否有纯条件路由混合的情况
    has_pure_conditions = node_config.get("_has_pure_conditions", False)
    pure_condition_node = node_config.get("_pure_condition_node", None)
    fallback_next_node = None
    
    # 从 _internal_branches 中查找 Fallback 分支的 _next_node
    internal_branches = node_config.get("_internal_branches", [])
    for branch in internal_branches:
        if branch.get("condition_name") == "Fallback" and "_next_node" in branch:
            fallback_next_node = branch.get("_next_node")
            break
    
    # 如果有纯条件路由，注册到 edge_manager
    if has_pure_conditions and fallback_next_node and pure_condition_node:
        edge_manager.register_semantic_judgment_with_pure_conditions(
            node_name, fallback_next_node, pure_condition_node
        )
```

### 3. 混合路由边生成逻辑

**位置**: `step6_workflow_generator.py` 第 1254-1318 行

在边生成逻辑后添加对混合路由的特殊处理：

```python
# 处理 semanticJudgment 节点的混合路由（Intent + 纯条件路由）
for semantic_node_name, mixed_routing_info in edge_manager.semantic_judgment_with_pure_conditions.items():
    fallback_next_node_name = mixed_routing_info["fallback_next_node"]
    pure_condition_node_name = mixed_routing_info["pure_condition_node"]
    
    # 1. 生成 SemanticJudgment → Fallback Text 的边
    if fallback_next_node_name in node_name_map:
        # 找到 Fallback 的 condition_id
        fallback_condition_id = None
        for node_config in node_configs:
            if node_config.get("name") == semantic_node_name:
                internal_branches = node_config.get("_internal_branches", [])
                for branch in internal_branches:
                    if branch.get("condition_name") == "Fallback":
                        fallback_condition_id = branch.get("condition_id")
                        break
                break
        
        if fallback_condition_id:
            # 使用 condition edge 连接 SemanticJudgment → Fallback Text
            fallback_edge = edge_manager._create_condition_edge(
                semantic_node_info, fallback_text_node_info, fallback_condition_id
            )
            edges.append(fallback_edge)
    
    # 2. 生成 Fallback Text → Pure Condition Node 的边
    if fallback_next_node_name in node_name_map and pure_condition_node_name in node_name_map:
        # 使用默认边连接 Fallback Text → Pure Condition Node
        pure_condition_edge = edge_manager._create_default_edge(
            fallback_text_node_info, pure_condition_node_info
        )
        edges.append(pure_condition_edge)
```

## 工作流程

1. **节点注册阶段**：
   - 处理 semanticJudgment 节点时，检查 `_has_pure_conditions` 标记
   - 如果有混合路由，提取 `fallback_next_node` 和 `pure_condition_node` 信息
   - 注册到 `edge_manager.semantic_judgment_with_pure_conditions`

2. **边生成阶段**：
   - 先处理常规的边（来自 edge_config.json 或默认顺序连接）
   - 遍历所有注册的混合路由 semanticJudgment 节点
   - 为每个混合路由节点生成两条特殊的边：
     - **边1**: SemanticJudgment → Fallback Text（使用 condition edge，condition_id 为 Fallback 的 condition_id）
     - **边2**: Fallback Text → Pure Condition Node（使用 default edge）

3. **Pure Condition Node 的分支边**：
   - Pure Condition Node 是一个普通的 condition 节点
   - 它的分支边通过 edge_config.json 定义
   - step6 会自动处理这些条件分支的边生成（通过标准的 condition edge 逻辑）

## 数据流

```
┌─────────────────────────────────┐
│  Step2: converter.py            │
│                                 │
│  生成节点配置：                  │
│  - semanticJudgment (带标记)     │
│    _has_pure_conditions: true   │
│    _pure_condition_node: "xxx"  │
│    _internal_branches: [...]    │
│  - fallback_text                │
│  - pure_condition_routing       │
│                                 │
└────────────┬────────────────────┘
             │ nodes_config.json
             ▼
┌─────────────────────────────────┐
│  Step6: workflow_generator.py   │
│                                 │
│  1. 读取节点配置                 │
│  2. 注册混合路由信息             │
│  3. 生成节点                     │
│  4. 生成常规边                   │
│  5. 生成混合路由的特殊边         │
│     - SemanticJudgment →        │
│       Fallback Text             │
│     - Fallback Text →           │
│       Pure Condition Node       │
│                                 │
└────────────┬────────────────────┘
             │ generated_workflow.json
             ▼
┌─────────────────────────────────┐
│  最终 Workflow                   │
│                                 │
│  包含完整的边连接：              │
│  - Intent 分支                   │
│  - Fallback → 纯条件判断         │
│  - 纯条件分支                    │
│                                 │
└─────────────────────────────────┘
```

## 关键字段说明

### 从 Step2 传递的字段

在 semanticJudgment 节点配置中：

- `_has_pure_conditions` (bool): 标记是否有纯条件路由
- `_pure_condition_node` (str): 纯条件判断节点名称
- `_internal_branches` (list): 内部分支信息
  - 每个分支包含：
    - `condition_id`: 条件ID（用于边连接）
    - `condition_name`: 条件名称
    - `_next_node`: 下一个节点（仅 Fallback 分支有此字段）
    - `_fallback_to_pure_condition`: 纯条件判断节点（仅 Fallback 分支有此字段）

### Step6 内部使用的字段

在 `node_name_map` 中：

- `_has_pure_conditions`: 标记（从配置复制）
- `_pure_condition_node`: 纯条件节点名（从配置复制）
- `_fallback_next_node`: Fallback 的下一个节点名（从 `_internal_branches` 提取）

## 边类型

1. **Condition Edge** (用于条件分支):
   ```json
   {
     "source": "semantic_judgment_node_id",
     "target": "fallback_text_node_id",
     "sourceHandle": "fallback_condition_handle_id",
     "targetHandle": "fallback_text_target_handle"
   }
   ```

2. **Default Edge** (用于顺序连接):
   ```json
   {
     "source": "fallback_text_node_id",
     "target": "pure_condition_node_id",
     "sourceHandle": "fallback_text_source_handle",
     "targetHandle": "pure_condition_target_handle"
   }
   ```

## 测试验证

使用以下测试数据验证：

1. **输入数据**: `input/7/exported_flow_MakingPayments_Fulfillment(router_muli_onec).json`
2. **Step2 输出**: nodes_config.json（包含 semanticJudgment 节点及其标记）
3. **Step6 输出**: generated_workflow.json（包含完整的边连接）

### 验证点

- [ ] SemanticJudgment 节点的 Fallback 分支连接到 Fallback Text 节点
- [ ] Fallback Text 节点连接到 Pure Condition Routing 节点
- [ ] Pure Condition Routing 节点的多个条件分支正确连接到目标页面
- [ ] Intent 分支正常连接到参数提取节点或目标页面

## 日志输出

实现了详细的日志输出，方便调试：

```
🔀 注册混合路由: semantic_judgment_xxx -> Fallback: fallback_text_xxx -> Pure Condition: pure_condition_routing_xxx
✅ 生成 Fallback 边: semantic_judgment_xxx → fallback_text_xxx
✅ 生成边: fallback_text_xxx → pure_condition_routing_xxx
```

## 后续优化

1. **错误处理**：添加更多的错误处理和验证逻辑
2. **配置验证**：在 step6 开始时验证节点配置的完整性
3. **边优化**：自动检测和删除重复的边
4. **可视化**：添加混合路由的可视化标记，方便在 UI 中识别

## 相关文件

- `step2/converter.py`: 生成节点配置（包含混合路由标记）
- `step6_workflow_generator.py`: 边生成逻辑实现
- `docs/Version2_Mixed_Routing_Implementation.md`: Step2 实现说明

