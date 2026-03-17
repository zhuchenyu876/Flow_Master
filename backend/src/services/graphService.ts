import { supabase } from '../config/supabase.js'
import { OpenAIEmbeddings } from '@langchain/openai'

const embeddings = new OpenAIEmbeddings({
  openAIApiKey: process.env.OPENAI_API_KEY,
})

export const saveGraph = async (prompt: string, mermaidCode: string) => {
  const embedding = await embeddings.embedQuery(prompt)

  const { data, error } = await supabase
    .from('graphs')
    .insert({
      title: prompt.substring(0, 100),
      prompt,
      embedding,
    })
    .select()
    .single()

  if (error) throw error

  // 解析 Mermaid 代码并创建节点
  await parseMermaidAndCreateNodes(data.id, mermaidCode)

  return data
}

export const findRelatedGraphs = async (prompt: string, currentGraphId: string) => {
  const embedding = await embeddings.embedQuery(prompt)

  const { data, error } = await supabase.rpc('match_graphs', {
    query_embedding: embedding,
    match_threshold: 0.7,
    match_count: 5,
  })

  if (error) throw error

  return data.filter((g: any) => g.id !== currentGraphId)
}

export const createGraphConnections = async (graphId: string, relatedGraphs: any[]) => {
  const edges = relatedGraphs.map((related) => ({
    graph_id: graphId,
    source_id: graphId,
    target_id: related.id,
    weight: related.similarity,
    label: 'related',
    rag_score: related.similarity,
  }))

  const { error } = await supabase.from('edges').insert(edges)

  if (error) throw error
}

const parseMermaidAndCreateNodes = async (graphId: string, mermaidCode: string) => {
  // 简单的 Mermaid 解析逻辑
  const lines = mermaidCode.split('\n')
  const nodes: any[] = []

  for (const line of lines) {
    const match = line.match(/(\w+)\[(.+?)\]/)
    if (match) {
      const [, id, label] = match
      nodes.push({
        graph_id: graphId,
        label,
        type: 'node',
        metadata: { originalId: id },
      })
    }
  }

  if (nodes.length > 0) {
    await supabase.from('nodes').insert(nodes)
  }
}
