import express from 'express'
import cors from 'cors'
import dotenv from 'dotenv'
import graphRoutes from './routes/graphs.js'
import documentRoutes from './routes/documents.js'

dotenv.config()

const app = express()
const PORT = process.env.PORT || 3001

app.use(cors())
app.use(express.json())

app.use('/api/graphs', graphRoutes)
app.use('/api/documents', documentRoutes)

app.get('/health', (req, res) => {
  res.json({ status: 'ok' })
})

app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`)
})
