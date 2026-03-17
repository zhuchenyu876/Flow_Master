import { useState } from 'react'
import { generateMermaid } from '../services/api'

interface PromptInputProps {
  onGraphGenerated: (graphId: string) => void
}

export default function PromptInput({ onGraphGenerated }: PromptInputProps) {
  const [prompt, setPrompt] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!prompt.trim()) return

    setLoading(true)
    try {
      const result = await generateMermaid(prompt)
      onGraphGenerated(result.graphId)
    } catch (error) {
      console.error('Failed to generate mermaid:', error)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h2 className="text-xl font-semibold mb-4">输入提示词</h2>
      <form onSubmit={handleSubmit}>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="描述你想要生成的图表..."
          className="w-full h-32 p-3 border rounded-lg resize-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          type="submit"
          disabled={loading}
          className="mt-4 w-full bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700 disabled:bg-gray-400"
        >
          {loading ? '生成中...' : '生成 Mermaid 图'}
        </button>
      </form>
    </div>
  )
}
