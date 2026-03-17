import { OpenAI } from '@langchain/openai'
import { PromptTemplate } from 'langchain/prompts'

const llm = new OpenAI({
  openAIApiKey: process.env.OPENAI_API_KEY,
  temperature: 0.7,
})

const mermaidPrompt = PromptTemplate.fromTemplate(`
You are an expert in creating Mermaid diagrams. Based on the user's description, generate a valid Mermaid diagram code.

User description: {prompt}

Generate only the Mermaid code without any explanation. Start with the diagram type (e.g., graph TD, sequenceDiagram, etc.).
`)

export const generateMermaidFromPrompt = async (prompt: string): Promise<string> => {
  try {
    const formattedPrompt = await mermaidPrompt.format({ prompt })
    const result = await llm.invoke(formattedPrompt)
    return result.trim()
  } catch (error) {
    console.error('Error generating mermaid:', error)
    throw error
  }
}
