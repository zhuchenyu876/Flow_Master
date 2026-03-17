import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'
import { getGraph } from '../services/api'

interface MermaidViewerProps {
  graphId: string | null
}

export default function MermaidViewer({ graphId }: MermaidViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [mermaidCode, setMermaidCode] = useState('')

  useEffect(() => {
    mermaid.initialize({ startOnLoad: false, theme: 'default' })
  }, [])

  useEffect(() => {
    if (!graphId) return

    const fetchGraph = async () => {
      try {
        const graph = await getGraph(graphId)
        setMermaidCode(graph.mermaidCode)
      } catch (error) {
        console.error('Failed to fetch graph:', error)
      }
    }

    fetchGraph()
  }, [graphId])

  useEffect(() => {
    if (!mermaidCode || !containerRef.current) return

    const renderMermaid = async () => {
      try {
        const { svg } = await mermaid.render('mermaid-diagram', mermaidCode)
        if (containerRef.current) {
          containerRef.current.innerHTML = svg
        }
      } catch (error) {
        console.error('Failed to render mermaid:', error)
      }
    }

    renderMermaid()
  }, [mermaidCode])

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h2 className="text-xl font-semibold mb-4">Mermaid 图表</h2>
      <div ref={containerRef} className="border rounded-lg p-4 min-h-[300px] overflow-auto" />
    </div>
  )
}
