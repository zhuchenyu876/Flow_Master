import fs from 'fs/promises'
import { supabase } from '../config/supabase.js'
import { OpenAIEmbeddings } from '@langchain/openai'

const embeddings = new OpenAIEmbeddings({
  openAIApiKey: process.env.OPENAI_API_KEY,
})

export const processDocument = async (file: Express.Multer.File) => {
  // 读取文件内容
  const content = await fs.readFile(file.path, 'utf-8')

  // 生成 embedding
  const embedding = await embeddings.embedQuery(content)

  // 保存到 Supabase Storage
  const { data: uploadData, error: uploadError } = await supabase.storage
    .from('documents')
    .upload(`${Date.now()}_${file.originalname}`, file.path)

  if (uploadError) throw uploadError

  // 保存元数据到数据库
  const { data, error } = await supabase
    .from('documents')
    .insert({
      filename: file.originalname,
      content,
      embedding,
      storage_path: uploadData.path,
    })
    .select()
    .single()

  if (error) throw error

  // 清理临时文件
  await fs.unlink(file.path)

  return data
}

export const searchInDocuments = async (query: string) => {
  const embedding = await embeddings.embedQuery(query)

  const { data, error } = await supabase.rpc('match_documents', {
    query_embedding: embedding,
    match_threshold: 0.7,
    match_count: 10,
  })

  if (error) throw error

  return data
}
