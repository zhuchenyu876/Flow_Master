import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api'

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

export const generateMermaid = async (prompt: string) => {
  const response = await api.post('/graphs/generate', { prompt })
  return response.data
}

export const getGraph = async (graphId: string) => {
  const response = await api.get(`/graphs/${graphId}`)
  return response.data
}

export const getGraphWithNodes = async (graphId: string) => {
  const response = await api.get(`/graphs/${graphId}/nodes`)
  return response.data
}

export const exportGraph = async (graphId: string, format: string) => {
  const response = await api.get(`/graphs/${graphId}/export/${format}`, {
    responseType: 'blob',
  })
  return response.data
}

export const uploadDocument = async (file: File) => {
  const formData = new FormData()
  formData.append('file', file)
  const response = await api.post('/documents/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  })
  return response.data
}
