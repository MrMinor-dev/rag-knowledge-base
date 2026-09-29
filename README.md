# RAG Knowledge Base

**Semantic search over 781 business documents on Postgres and pgvector, and the document rules that make retrieval work.**

I wanted an AI agent to find the right business context without me in the room. It failed the first time I tried, and the reason wasn't the model.

Retrieval breaks documents into chunks and finds the closest ones to a question. Each chunk arrives alone. My documents were written for a person reading top to bottom. A section called "Overview" said "the system" and never named which one. Another said "See Section 4." A third mentioned "Phase 0" and assumed you knew what that was.

I asked what the budget limits were for one specific business. Search returned generic text about budgets from a different one. Every document was accurate. Retrieval still failed, because a chunk had no way to say what it was about.

The fix was to rewrite the documents so every chunk carries its own context, and then to score them so I could tell when it worked.

## What's here

| File | What it is |
|---|---|
| [sql/pgvector-schema.sql](sql/pgvector-schema.sql) | The chunk table: 768-dimension vectors, an HNSW cosine index, a unique key on file and chunk number. |
| [sql/semantic-search.sql](sql/semantic-search.sql) | The search function: closest chunks above a similarity threshold. |
| [workflows/knowledge/haios-database-semantic-search.json](workflows/knowledge/haios-database-semantic-search.json) | The n8n workflow that takes a question, embeds it, and returns the closest chunks. Sanitized export. |
| [indexer/semantic_index.py](indexer/semantic_index.py) | The incremental indexer. It re-embeds only files whose content hash changed. Two hardcoded local paths were replaced with environment variables. |
| [indexer/requirements.txt](indexer/requirements.txt) | Its dependencies. |
| [CHUNKING-RUBRIC.md](CHUNKING-RUBRIC.md) | The 100-point chunk quality score and the 5 document tiers. |
| [LICENSE](LICENSE) | MIT. |

The knowledge-base [wiki workflows](https://github.com/MrMinor-dev/n8n-development-framework/tree/main/workflows/knowledge) live in the n8n repo.

## How the pipeline works

```
Markdown documents
    -> hash each file, skip the unchanged ones
    -> split into chunks of 750 characters with 150 overlap
    -> embed each chunk (all-mpnet-base-v2, 768 dimensions)
    -> upsert into Postgres with pgvector (HNSW index, cosine distance)

Question
    -> embed the question the same way
    -> semantic_search() returns the closest chunks above a similarity threshold
    -> an n8n webhook returns them, with file paths, to the agent
```

- **Corpus:** 781 documents: strategy, finance, operating procedures, skills, and workflow READMEs.
- **Incremental updates:** the indexer hashes each file's content and skips anything unchanged, so a routine update takes seconds. The write is an upsert, so a run that dies halfway can be run again.
- **Left out on purpose:** conversation transcripts. They dominated the index with filler, so it holds documents only. Credentials and build logs are excluded too.
- **Health logging:** the search workflow logs each success and error to the shared health table.

## What makes a chunk retrievable

I wrote 5 rules and turned them into a score.

1. **Chunk independence.** Every section stands alone. Before: "The process involves three steps." After: "[Business name] content creation process involves three steps."
2. **Context repetition.** The business name, model, and key constraints show up in every major section, even though that breaks the DRY habit humans have.
3. **Keyword density.** The business name appears enough times that a search for it lands on the right document.
4. **Explicit references.** "See Section 4" becomes the full document path and section title. A retrieved chunk can't follow a vague link.
5. **Specific headings.** "Overview" becomes a heading that names the business and the topic.

The full rubric, with the 100-point score and the 5 document tiers, is in [CHUNKING-RUBRIC.md](CHUNKING-RUBRIC.md).

### Results of the first pass

The first optimization pass covered 84 critical documents. Scores rose from a range of 45 to 62, up to 70 to 97. A document had to pass every quality gate to be accepted, and none needed a second rewrite.

To classify 84 documents without scoring all of them, I scored 4 diverse ones, found the patterns they shared, and assigned tiers by pattern. After 4 documents by hand confirmed the rules worked, I scripted the transformation with Claude Code. One session rewrote 64 documents. The work went into git commits, so any single rewrite could be rolled back.

## What I would tell a team starting this

- Rewriting documents for retrieval is different work than editing them for readers. Generic headings and "see above" cost you the most.
- Score before you scale. Objective gates make batch work consistent.
- Keep the index current. A stale index returns confident answers about things that no longer exist.
- Decide what stays out of the index as carefully as what goes in.

## Built with

Supabase (Postgres with pgvector), the `all-mpnet-base-v2` embedding model, n8n, Python, and Claude Code.

---

Jordan Waxman · [LinkedIn](https://linkedin.com/in/waxmanjordan) · [GitHub profile](https://github.com/MrMinor-dev)
