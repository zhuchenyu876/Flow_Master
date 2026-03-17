import { Request, Response } from 'express'
import { processDocument, searchInDocuments } from '../services/documentService.js'

export const uploadDocument = async (req: Request, res: Response) => {
  try {
    if (!req.file) {
      return res.status(400).json({ error: 'No file uploaded' })
    }

    const result = await processDocument(req.file)
    res.json(result)
  } catch (error) {
    console.error('Error uploading document:', error)
    res.status(500).json({ error: 'Failed to upload document' })
  }
}

export const searchDocuments = async (req: Request, res: Response) => {
  try {
    const { query } = req.body
    const results = await searchInDocuments(query)
    res.json(results)
  } catch (error) {
    console.error('Error searching documents:', error)
    res.status(500).json({ error: 'Failed to search documents' })
  }
}
