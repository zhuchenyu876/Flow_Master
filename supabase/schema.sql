-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Graphs table
CREATE TABLE graphs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT NOT NULL,
  prompt TEXT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  embedding vector(1536)
);

-- Nodes table
CREATE TABLE nodes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  graph_id UUID REFERENCES graphs(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  type TEXT NOT NULL,
  metadata JSONB,
  embedding vector(1536),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Edges table
CREATE TABLE edges (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  graph_id UUID REFERENCES graphs(id) ON DELETE CASCADE,
  source_id UUID,
  target_id UUID,
  weight FLOAT DEFAULT 1.0,
  label TEXT,
  rag_score FLOAT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Documents table
CREATE TABLE documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  filename TEXT NOT NULL,
  content TEXT NOT NULL,
  storage_path TEXT,
  embedding vector(1536),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Exports table
CREATE TABLE exports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  graph_id UUID REFERENCES graphs(id) ON DELETE CASCADE,
  format TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  status TEXT DEFAULT 'pending',
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes
CREATE INDEX idx_graphs_embedding ON graphs USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX idx_nodes_embedding ON nodes USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX idx_documents_embedding ON documents USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX idx_nodes_graph_id ON nodes(graph_id);
CREATE INDEX idx_edges_graph_id ON edges(graph_id);

-- Function to match similar graphs
CREATE OR REPLACE FUNCTION match_graphs(
  query_embedding vector(1536),
  match_threshold float,
  match_count int
)
RETURNS TABLE (
  id UUID,
  title TEXT,
  prompt TEXT,
  similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    graphs.id,
    graphs.title,
    graphs.prompt,
    1 - (graphs.embedding <=> query_embedding) AS similarity
  FROM graphs
  WHERE 1 - (graphs.embedding <=> query_embedding) > match_threshold
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;

-- Function to match similar documents
CREATE OR REPLACE FUNCTION match_documents(
  query_embedding vector(1536),
  match_threshold float,
  match_count int
)
RETURNS TABLE (
  id UUID,
  filename TEXT,
  content TEXT,
  similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    documents.id,
    documents.filename,
    documents.content,
    1 - (documents.embedding <=> query_embedding) AS similarity
  FROM documents
  WHERE 1 - (documents.embedding <=> query_embedding) > match_threshold
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;
