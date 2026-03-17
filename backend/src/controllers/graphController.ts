import { Request, Response } from 'express'
import { generateMermaidFromPrompt } from '../services/llmService.js'
import { saveGraph, findRelatedGraphs, createGraphConnections } from '../services/graphService.js'
import { supabase } from '../config/supabase.js'

export const generateMermaidGraph = async (req: Request, res: Response) => {
  try {
    const { prompt } = req.body

    // 1. 使用 LLM 生成 Mermaid 代码
    const mermaidCode = await generateMermaidFromPrompt(prompt)

    // 2. 保存到数据库
    const graph = await saveGraph(prompt, mermaidCode)

    // 3. RAG 搜索相关图表
    const relatedGraphs = await findRelatedGraphs(prompt, graph.id)

    // 4. 创建 DAG 连接
    if (relatedGraphs.length > 0) {
      await createGraphConnections(graph.id, relatedGraphs)
    }

    res.json({ graphId: graph.id, mermaidCode })
  } catch (error) {
    console.error('Error generating mermaid:', error)
    res.status(500).json({ error: 'Failed to generate mermaid' })
  }
}

export const getGraph = async (req: Request, res: Response) => {
  try {
    const { id } = req.params

    const { data, error } = await supabase
      .from('graphs')
      .select('*')
      .eq('id', id)
      .single()

    if (error) throw error

    res.json(data)
  } catch (error) {
    console.error('Error fetching graph:', error)
    res.status(500).json({ error: 'Failed to fetch graph' })
  }
}

export const getGraphWithNodes = async (req: Request, res: Response) => {
  try {
    const { id } = req.params

    const { data: nodes, error: nodesError } = await supabase
      .from('nodes')
      .select('*')
      .eq('graph_id', id)

    if (nodesError) throw nodesError

    const { data: edges, error: edgesError } = await supabase
      .from('edges')
      .select('*')
      .eq('graph_id', id)

    if (edgesError) throw edgesError

    res.json({ nodes, edges })
  } catch (error) {
    console.error('Error fetching graph nodes:', error)
    res.status(500).json({ error: 'Failed to fetch graph nodes' })
  }
}

export const exportGraphToFormat = async (req: Request, res: Response) => {
  try {
    const { id, format } = req.params

    // TODO: 实现导出逻辑
    res.status(501).json({ error: 'Export not implemented yet' })
  } catch (error) {
    console.error('Error exporting graph:', error)
    res.status(500).json({ error: 'Failed to export graph' })
  }
}
