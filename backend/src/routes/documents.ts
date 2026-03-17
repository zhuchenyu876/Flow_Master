import { Router } from 'express'
import multer from 'multer'
import { uploadDocument, searchDocuments } from '../controllers/documentController.js'

const router = Router()
const upload = multer({ dest: 'uploads/' })

router.post('/upload', upload.single('file'), uploadDocument)
router.post('/search', searchDocuments)

export default router
