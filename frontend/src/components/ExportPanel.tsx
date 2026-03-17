import { useState } from 'react'
import { exportGraph } from '../services/api'

interface ExportPanelProps {
  graphId: string | null
}

export default function ExportPanel({ graphId }: ExportPanelProps) {
  const [exporting, setExporting] = useState(false)

  const handleExport = async (format: 'ppt' | 'pdf' | 'word' | 'json') => {
    if (!graphId) return

    setExporting(true)
    try {
      const blob = await exportGraph(graphId, format)
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `graph-${graphId}.${format}`
      a.click()
      window.URL.revokeObjectURL(url)
    } catch (error) {
      console.error('Failed to export:', error)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h2 className="text-xl font-semibold mb-4">导出选项</h2>
      <div className="grid grid-cols-2 gap-3">
        <button
          onClick={() => handleExport('ppt')}
          disabled={!graphId || exporting}
          className="py-2 px-4 bg-orange-600 text-white rounded hover:bg-orange-700 disabled:bg-gray-300"
        >
          导出 PPT
        </button>
        <button
          onClick={() => handleExport('pdf')}
          disabled={!graphId || exporting}
          className="py-2 px-4 bg-red-600 text-white rounded hover:bg-red-700 disabled:bg-gray-300"
        >
          导出 PDF
        </button>
        <button
          onClick={() => handleExport('word')}
          disabled={!graphId || exporting}
          className="py-2 px-4 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-gray-300"
        >
          导出 Word
        </button>
        <button
          onClick={() => handleExport('json')}
          disabled={!graphId || exporting}
          className="py-2 px-4 bg-green-600 text-white rounded hover:bg-green-700 disabled:bg-gray-300"
        >
          导出 JSON
        </button>
      </div>
    </div>
  )
}
