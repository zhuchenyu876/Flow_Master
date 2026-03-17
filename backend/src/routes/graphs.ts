import { Router } from 'express'
import { generateMermaidGraph, getGraph, getGraphWithNodes, exportGraphToFormat } from '../controllers/graphController.js'

const router = Router()

router.post('/generate', generateMermaidGraph)
router.get('/:id', getGraph)
router.get('/:id/nodes', getGraphWithNodes)
router.get('/:id/export/:format', exportGraphToFormat)

export default router
