import { useEffect, useState } from 'react'
import { getGraphWithNodes } from '../services/api'

interface GraphViewerProps {
  graphId: string | null
}

interface Node {
  id: string
  label: string
  type: string
}

interface Edge {
  source: string
  target: string
  label?: string
}

export default function GraphViewer({ graphId }: GraphViewerProps) {
  const [nodes, setNodes] = useState<Node[]>([])
  const [edges, setEdges] = useState<Edge[]>([])

  useEffect(() => {
    if (!graphId) return

    const fetchGraphData = async () => {
      try {
        const data = await getGraphWithNodes(graphId)
        setNodes(data.nodes)
        setEdges(data.edges)
      } catch (error) {
        console.error('Failed to fetch graph data:', error)
      }
    }

    fetchGraphData()
  }, [graphId])

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h2 className="text-xl font-semibold mb-4">DAG 关系图</h2>
      <div className="border rounded-lg p-4 min-h-[400px]">
        {nodes.length > 0 ? (
          <div className="space-y-2">
            <p className="text-sm text-gray-600">节点数: {nodes.length}</p>
            <p className="text-sm text-gray-600">边数: {edges.length}</p>
            {/* TODO: 集成 React Flow 或 D3.js 进行可视化 */}
          </div>
        ) : (
          <p className="text-gray-400 text-center py-8">暂无图数据</p>
        )}
      </div>
    </div>
  )
}
