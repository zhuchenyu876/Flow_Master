import { useState } from 'react'
import PromptInput from './components/PromptInput'
import MermaidViewer from './components/MermaidViewer'
import GraphViewer from './components/GraphViewer'
import ExportPanel from './components/ExportPanel'

function App() {
  const [currentGraphId, setCurrentGraphId] = useState<string | null>(null)

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow-sm">
        <div className="max-w-7xl mx-auto px-4 py-4">
          <h1 className="text-2xl font-bold text-gray-900">Mermaid RAG Graph</h1>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="space-y-6">
            <PromptInput onGraphGenerated={setCurrentGraphId} />
            <MermaidViewer graphId={currentGraphId} />
          </div>

          <div className="space-y-6">
            <GraphViewer graphId={currentGraphId} />
            <ExportPanel graphId={currentGraphId} />
          </div>
        </div>
      </main>
    </div>
  )
}

export default App
