-- Vector table for document chunks (768-dim, HNSW cosine index)
-- Reconstructed from the live schema on 2026-09-29 (read-only). Constraints and indexes included; data not.

CREATE TABLE public.doc_embeddings (
  id uuid DEFAULT gen_random_uuid() NOT NULL,
  file_path text NOT NULL,
  chunk_index integer NOT NULL,
  content text NOT NULL,
  embedding vector(768),
  metadata jsonb DEFAULT '{}'::jsonb,
  created_at timestamp with time zone DEFAULT now(),
  updated_at timestamp with time zone DEFAULT now(),
  content_hash text,
  CONSTRAINT doc_embeddings_pkey PRIMARY KEY (id),
  CONSTRAINT doc_embeddings_file_path_chunk_index_key UNIQUE (file_path, chunk_index)
);
CREATE INDEX doc_embeddings_embedding_idx ON public.doc_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX doc_embeddings_file_path_idx ON public.doc_embeddings USING btree (file_path);
CREATE INDEX idx_doc_embeddings_hash ON public.doc_embeddings USING btree (file_path, content_hash);
ALTER TABLE public.doc_embeddings ENABLE ROW LEVEL SECURITY;

