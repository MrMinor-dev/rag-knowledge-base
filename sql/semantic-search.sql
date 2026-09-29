-- Vector search: returns the closest chunks above a similarity threshold (cosine).
-- Live definition, 2026-09-29.

CREATE OR REPLACE FUNCTION public.semantic_search(query_embedding vector, match_count integer DEFAULT 5, similarity_threshold double precision DEFAULT 0.5);
