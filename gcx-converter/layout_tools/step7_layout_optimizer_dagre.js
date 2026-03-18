/**
 * Step 7: 工作流布局优化 - Dagre 版本
 * 使用 dagre 库来美化生成的 workflow JSON 文件的节点位置
 * 
 * 这个脚本直接使用 dagre 库，与原始 JavaScript 代码逻辑完全一致
 */

// 尝试使用 @dagrejs/dagre，如果不存在则使用 dagre
let dagre;
try {
  dagre = require('@dagrejs/dagre');
} catch (e) {
  dagre = require('dagre');
}
const fs = require('fs');
const path = require('path');

// 默认节点尺寸
const DEFAULT_NODE_WIDTH = 200;
const DEFAULT_NODE_HEIGHT = 100;

// 节点类型对应的尺寸
const NODE_DIMENSIONS = {
  'start': { width: 150, height: 50 },
  'block': { width: 250, height: 150 },
  'default': { width: DEFAULT_NODE_WIDTH, height: DEFAULT_NODE_HEIGHT }
};

/**
 * 获取节点尺寸
 */
function getNodeDimensions(node) {
  const nodeType = node.type || 'default';
  const dimensions = NODE_DIMENSIONS[nodeType] || NODE_DIMENSIONS.default;
  
  // 如果节点有 blockId，可能是 block 节点
  if (node.blockId) {
    return NODE_DIMENSIONS.block;
  }
  
  return dimensions;
}

/**
 * 获取最大节点尺寸（用于居中计算）
 */
function getMaxDimensions(nodes) {
  let maxWidth = 0;
  let maxHeight = 0;
  
  for (const node of nodes) {
    const dims = getNodeDimensions(node);
    maxWidth = Math.max(maxWidth, dims.width);
    maxHeight = Math.max(maxHeight, dims.height);
  }
  
  return { maxWidth, maxHeight };
}

/**
 * 优化工作流布局
 */
function optimizeLayout(workflowData, direction = 'LR') {
  const nodes = workflowData.nodes || [];
  const edges = workflowData.edges || [];
  
  if (nodes.length === 0) {
    return workflowData;
  }
  
  // Silent processing - no verbose output
  
  // 创建新的 dagre 图
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));
  
  const isHorizontal = direction === 'LR';
  dagreGraph.setGraph({ 
    rankdir: direction,
    nodesep: 50,  // 节点之间的最小间距
    ranksep: 100, // 层之间的最小间距
    edgesep: 20   // 边之间的最小间距
  });
  
  // 获取最大节点尺寸
  const { maxWidth, maxHeight } = getMaxDimensions(nodes);
  
  // 添加节点到 dagre 图
  for (const node of nodes) {
    const dims = getNodeDimensions(node);
    dagreGraph.setNode(node.id, {
      width: dims.width,
      height: dims.height
    });
  }
  
  // 添加边到 dagre 图
  for (const edge of edges) {
    if (edge.source && edge.target) {
      dagreGraph.setEdge(edge.source, edge.target);
    }
  }
  
  // 执行布局计算
  dagre.layout(dagreGraph);
  
  // 更新节点位置
  let updatedCount = 0;
  for (const node of nodes) {
    const nodeWithPosition = dagreGraph.node(node.id);
    if (nodeWithPosition) {
      const dims = getNodeDimensions(node);
      const nodeTop = (maxHeight - dims.height) / 2;
      const nodeLeft = (maxWidth - dims.width) / 2;
      
      if (isHorizontal) {
        // 从左到右布局
        node.position = {
          x: nodeWithPosition.x,
          y: nodeWithPosition.y + nodeTop
        };
        
        // 设置 source 和 target 位置
        if (!node.data) {
          node.data = {};
        }
        node.data.targetPosition = 'left';
        node.data.sourcePosition = 'right';
      } else {
        // 从上到下布局
        node.position = {
          x: nodeWithPosition.x + nodeLeft,
          y: nodeWithPosition.y
        };
        
        // 设置 source 和 target 位置
        if (!node.data) {
          node.data = {};
        }
        node.data.targetPosition = 'top';
        node.data.sourcePosition = 'bottom';
      }
      
      updatedCount++;
    }
  }
  
  // 更新边的位置
  for (const edge of edges) {
    const sourceNode = nodes.find(n => n.id === edge.source);
    const targetNode = nodes.find(n => n.id === edge.target);
    
    if (sourceNode && targetNode && sourceNode.position && targetNode.position) {
      const sourceDims = getNodeDimensions(sourceNode);
      const targetDims = getNodeDimensions(targetNode);
      
      if (isHorizontal) {
        // 从左到右：source 从右边出，target 从左边进
        edge.sourceX = sourceNode.position.x + sourceDims.width;
        edge.sourceY = sourceNode.position.y + sourceDims.height / 2;
        edge.targetX = targetNode.position.x;
        edge.targetY = targetNode.position.y + targetDims.height / 2;
      } else {
        // 从上到下：source 从下边出，target 从上边进
        edge.sourceX = sourceNode.position.x + sourceDims.width / 2;
        edge.sourceY = sourceNode.position.y + sourceDims.height;
        edge.targetX = targetNode.position.x + targetDims.width / 2;
        edge.targetY = targetNode.position.y;
      }
    }
  }
  
  // Return count for summary output
  workflowData._layoutStats = { nodes: updatedCount, edges: edges.length };
  
  return workflowData;
}

/**
 * 主函数
 */
function main() {
  const args = process.argv.slice(2);
  
  // 解析参数
  let inputFile = null;
  let outputFile = null;
  let direction = 'LR';
  
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--file' || args[i] === '-f') {
      inputFile = args[++i];
    } else if (args[i] === '--output' || args[i] === '-o') {
      outputFile = args[++i];
    } else if (args[i] === '--direction' || args[i] === '-d') {
      direction = args[++i] || 'LR';
    } else if (args[i] === '--input' || args[i] === '-i') {
      // 批量处理模式
      const inputDir = args[++i] || 'output/step6_final';
      processAllWorkflows(inputDir, outputFile, direction);
      return;
    }
  }
  
  if (!inputFile) {
    console.error('❌ Error: --file (-f) parameter is required');
    process.exit(1);
  }
  
  if (!fs.existsSync(inputFile)) {
    console.error(`❌ Error: Input file not found: ${inputFile}`);
    process.exit(1);
  }
  
  // 读取工作流数据
  const workflowData = JSON.parse(fs.readFileSync(inputFile, 'utf-8'));
  
  // 优化布局
  const optimizedData = optimizeLayout(workflowData, direction);
  
  // 保存结果
  if (!outputFile) {
    outputFile = inputFile;
  }
  
  // 获取布局统计信息
  const stats = optimizedData._layoutStats || { nodes: 0, edges: 0 };
  delete optimizedData._layoutStats;  // 不保存到文件中
  
  fs.writeFileSync(outputFile, JSON.stringify(optimizedData, null, 2), 'utf-8');
  
  // 单行输出
  const fileName = path.basename(outputFile);
  // console.log(`  ✅ Step 7 Dagre布局: ${fileName} (${stats.nodes} nodes, ${stats.edges} edges, ${direction})`);
}

/**
 * 批量处理所有工作流文件
 */
function processAllWorkflows(inputDir, outputDir, direction) {
  if (!outputDir) {
    outputDir = inputDir;
  }
  
  if (!fs.existsSync(inputDir)) {
    console.error(`⚠️  Warning: Input directory not found: ${inputDir}`);
    return;
  }
  
  // 确保输出目录存在
  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }
  
  // 查找所有 workflow JSON 文件
  const files = fs.readdirSync(inputDir);
  const workflowFiles = files.filter(f => 
    f.startsWith('generated_workflow_') && f.endsWith('.json')
  );
  
  if (workflowFiles.length === 0) {
    console.error(`⚠️  Warning: No workflow files found in ${inputDir}`);
    return;
  }
  
  let successCount = 0;
  let totalNodes = 0;
  
  for (const workflowFile of workflowFiles) {
    const inputPath = path.join(inputDir, workflowFile);
    const outputPath = path.join(outputDir, workflowFile);
    
    try {
      const workflowData = JSON.parse(fs.readFileSync(inputPath, 'utf-8'));
      const optimizedData = optimizeLayout(workflowData, direction);
      
      const stats = optimizedData._layoutStats || { nodes: 0 };
      delete optimizedData._layoutStats;
      totalNodes += stats.nodes;
      
      fs.writeFileSync(outputPath, JSON.stringify(optimizedData, null, 2), 'utf-8');
      successCount++;
    } catch (error) {
      console.error(`  ❌ Step 7 Dagre布局失败: ${workflowFile} - ${error.message}`);
    }
  }
  
  // console.log(`  ✅ Step 7 Dagre批量布局: ${successCount}/${workflowFiles.length} files, ${totalNodes} nodes, ${direction}`);
}

// 运行主函数
if (require.main === module) {
  main();
}

module.exports = { optimizeLayout, processAllWorkflows };

