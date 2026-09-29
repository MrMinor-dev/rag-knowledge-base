"""
Semantic Index Script - MRMINOR
Smart incremental reindex with content hash checking

Usage:
  python semantic_index.py                    # Smart reindex (only changed files)
  python semantic_index.py --files file1.md file2.md   # Update specific files
  python semantic_index.py --full             # Force full reindex (TRUNCATE + re-embed all)
"""

import os
import sys
import json
import argparse
import requests
import psycopg2
import hashlib
from pathlib import Path
import time

def _read_secret(filename):
    """Read a secret from the .secrets directory. Exits with a clear error if not found."""
    secrets_path = Path(os.environ.get("SECRETS_DIR", ".secrets")) / filename
    try:
        return secrets_path.read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        print(f"ERROR: Secret file not found: {secrets_path}")
        print(f"  Save the value to that file and retry.")
        sys.exit(1)

# ============ CONFIGURATION ============
HUGGINGFACE_TOKEN = _read_secret("huggingface-token.txt")
SUPABASE_CONNECTION = _read_secret("supabase-db-connection.txt")

DOCS_PATH = os.environ.get("DOCS_PATH", ".")
CHUNK_SIZE = 750
CHUNK_OVERLAP = 150
MIN_CHUNK_LENGTH = 20
BATCH_SIZE = 32

HF_URL = "https://router.huggingface.co/hf-inference/models/sentence-transformers/all-mpnet-base-v2/pipeline/feature-extraction"

# Folders to exclude from indexing
# Rationale (-followup): scope semantic index to doctrine/decision/SSOT content only.
# - Transcripts: chronological recall is via conversation_search; structured content is via proof-extraction-skill (Pass 2+).
#   Indexing them generated 12k+ batches and dominated the index with conversational filler.
# - Proof-Extraction-Outputs: JSONL audit trails + sample JSONs are diagnostic data, not search targets.
# - .secrets / .claude / venv / _archived: defensive — never index credentials, project-local CC config, or dev artifacts.
# Strong-keep categories: AOS/Skills, AOS/CC-Config, AOS/Workflows, HAIOS (excluding Sessions/Transcripts), AGENCY *.md root,
# Archive is flat and excluded via 'Archive'; CC-BUILD-LOG-/CC-FEEDBACK- also excluded by filename prefix (build-logs + feedback aren't search targets). CC-PROMPT- stays indexed (active working prompts).
EXCLUDE_FOLDERS = {
    'Archive',
    '.git',
    '__pycache__',
    'node_modules',
    'Transcripts',
    'Proof-Extraction-Outputs',
    '.secrets',
    '.claude',
    'venv',
    '_archived',
}

# Filename patterns to exclude (defensive against secret-like files in any directory)
EXCLUDE_FILENAME_PREFIXES = ('secret-', 'credential-', '.env', 'CC-BUILD-LOG-', 'CC-FEEDBACK-')

# ============ FUNCTIONS ============

def get_md_files(base_path):
    """Recursively find all .md files"""
    md_files = []
    for root, dirs, files in os.walk(base_path):
        # Exclude hidden folders and archive
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in EXCLUDE_FOLDERS]
        for file in files:
            if file.endswith('.md') and not file.startswith(EXCLUDE_FILENAME_PREFIXES):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, base_path)
                md_files.append((full_path, rel_path))
    return md_files

def find_file(base_path, filename):
    """Find a file by name or relative path (searches recursively)"""
    # Normalize the input path separators
    normalized_input = filename.replace('\\', '/').replace('/', os.sep)

    for root, dirs, files in os.walk(base_path):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in EXCLUDE_FOLDERS]
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, base_path)
            # Check if rel_path matches (handles HAIOS/Skills/SKILL.md style input)
            if rel_path == normalized_input or rel_path.replace('\\', '/') == filename.replace('\\', '/'):
                return (full_path, rel_path)
            # Also check by filename only as fallback
            if file == filename or file.endswith(filename):
                return (full_path, rel_path)
    return None

def get_file_hash(file_path):
    """Compute MD5 hash of file content"""
    with open(file_path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

def get_existing_hashes(cur):
    """Get all existing file hashes from database"""
    cur.execute("SELECT DISTINCT file_path, content_hash FROM doc_embeddings WHERE content_hash IS NOT NULL")
    return {row[0]: row[1] for row in cur.fetchall()}

def chunk_text(text, file_path):
    """Split text into overlapping chunks"""
    chunks = []
    start = 0
    index = 0

    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunk = text[start:end]

        if len(chunk.strip()) >= MIN_CHUNK_LENGTH:
            chunks.append({
                'file_path': file_path,
                'chunk_index': index,
                'content': chunk,
                'metadata': json.dumps({
                    'file_name': os.path.basename(file_path),
                    'chunk_size': len(chunk)
                })
            })
            index += 1

        start += CHUNK_SIZE - CHUNK_OVERLAP

    return chunks

def get_batch_embeddings(texts, retries=3):
    """Get embeddings for multiple texts in one API call"""
    headers = {
        "Authorization": f"Bearer {HUGGINGFACE_TOKEN}",
        "Content-Type": "application/json"
    }

    for attempt in range(retries):
        try:
            response = requests.post(
                HF_URL,
                headers=headers,
                json={"inputs": texts},
                timeout=120
            )

            if response.status_code == 200:
                embeddings = response.json()
                if isinstance(embeddings, list) and len(embeddings) == len(texts):
                    valid = all(isinstance(e, list) and len(e) == 768 for e in embeddings)
                    if valid:
                        return embeddings
                return None
            elif response.status_code == 503:
                print(f"    Model loading, waiting 20s...")
                time.sleep(20)
            else:
                print(f"    API error {response.status_code}: {response.text[:200]}")
                time.sleep(2)

        except Exception as e:
            print(f"    Request error: {e}")
            time.sleep(2)

    return None

def get_db_connection():
    """Get a fresh database connection"""
    conn = psycopg2.connect(SUPABASE_CONNECTION)
    conn.autocommit = False
    return conn

def process_files(conn, cur, md_files, mode="update", file_hashes=None):
    """Process files and insert embeddings. Returns (total_inserted, conn, cur)

    file_hashes: dict mapping rel_path -> content_hash (optional)
    """
    all_chunks = []
    if file_hashes is None:
        file_hashes = {}

    for full_path, rel_path in md_files:
        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()

            if len(text.strip()) >= MIN_CHUNK_LENGTH:
                # Compute hash if not provided
                if rel_path not in file_hashes:
                    file_hashes[rel_path] = get_file_hash(full_path)

                chunks = chunk_text(text, rel_path)
                # Add hash to each chunk
                for chunk in chunks:
                    chunk['content_hash'] = file_hashes[rel_path]
                all_chunks.extend(chunks)
                print(f"   {rel_path}: {len(chunks)} chunks")

        except Exception as e:
            print(f"   Error reading {rel_path}: {e}")

    if not all_chunks:
        print("   No chunks to process!")
        return 0, conn, cur

    print(f"\n   Total: {len(all_chunks)} chunks from {len(md_files)} files")
    print(f"\n3. Getting embeddings (batch size={BATCH_SIZE})...")

    total_inserted = 0
    failed_batches = 0

    for batch_start in range(0, len(all_chunks), BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, len(all_chunks))
        batch = all_chunks[batch_start:batch_end]
        batch_num = (batch_start // BATCH_SIZE) + 1
        total_batches = (len(all_chunks) + BATCH_SIZE - 1) // BATCH_SIZE

        texts = [c['content'] for c in batch]
        print(f"   Batch {batch_num}/{total_batches} ({len(batch)} chunks)...", end=" ", flush=True)

        embeddings = get_batch_embeddings(texts)

        if embeddings is None:
            print("FAILED")
            failed_batches += 1
            continue

        retry_insert = True
        while retry_insert:
            retry_insert = False
            try:
                for chunk, embedding in zip(batch, embeddings):
                    cur.execute("""
                        INSERT INTO doc_embeddings (file_path, chunk_index, content, embedding, metadata, content_hash)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (file_path, chunk_index)
                        DO UPDATE SET
                            content = EXCLUDED.content,
                            embedding = EXCLUDED.embedding,
                            metadata = EXCLUDED.metadata,
                            content_hash = EXCLUDED.content_hash,
                            updated_at = NOW()
                    """, (
                        chunk['file_path'],
                        chunk['chunk_index'],
                        chunk['content'],
                        json.dumps(embedding),
                        chunk['metadata'],
                        chunk.get('content_hash')
                    ))
                    total_inserted += 1
                conn.commit()
                print(f"OK")
            except (psycopg2.InterfaceError, psycopg2.OperationalError) as e:
                print(f"\n    Connection lost, reconnecting...")
                try:
                    conn.close()
                except:
                    pass
                time.sleep(2)
                conn = get_db_connection()
                cur = conn.cursor()
                retry_insert = True
                total_inserted -= len(batch)
            except Exception as e:
                print(f"\n    DB error: {e}")

        time.sleep(0.3)

    return total_inserted, conn, cur

def full_reindex():
    """Full reindex - clears all embeddings and rebuilds"""
    print("=" * 60)
    print("SEMANTIC INDEX - FULL REINDEX")
    print("=" * 60)

    print("\n1. Connecting to Supabase...")
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        print("   Connected!")
    except Exception as e:
        print(f"   ERROR: {e}")
        return

    print("\n2. Clearing existing embeddings...")
    cur.execute("TRUNCATE TABLE doc_embeddings;")
    conn.commit()
    print("   Cleared!")

    print(f"\n3. Scanning {DOCS_PATH}...")
    md_files = get_md_files(DOCS_PATH)
    print(f"   Found {len(md_files)} markdown files")

    print("\n4. Processing files...")
    total, conn, cur = process_files(conn, cur, md_files, mode="full")

    print("\n" + "=" * 60)
    print("REINDEX COMPLETE")
    print("=" * 60)
    print(f"Chunks indexed: {total}")

    cur.execute("SELECT COUNT(*) FROM doc_embeddings;")
    print(f"Database total: {cur.fetchone()[0]}")

    cur.close()
    conn.close()

def update_files(file_list):
    """Incremental update - only updates specified files"""
    print("=" * 60)
    print("SEMANTIC INDEX - INCREMENTAL UPDATE")
    print("=" * 60)
    print(f"Files to update: {file_list}")

    print("\n1. Connecting to Supabase...")
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        print("   Connected!")
    except Exception as e:
        print(f"   ERROR: {e}")
        return

    # Find and validate files
    md_files = []
    for filename in file_list:
        result = find_file(DOCS_PATH, filename)
        if result:
            md_files.append(result)
            print(f"   Found: {result[1]}")
        else:
            print(f"   NOT FOUND: {filename}")

    if not md_files:
        print("\nNo valid files to process!")
        return

    # Delete existing embeddings for these files
    print("\n2. Removing old embeddings...")
    for full_path, rel_path in md_files:
        cur.execute("DELETE FROM doc_embeddings WHERE file_path = %s", (rel_path,))
        deleted = cur.rowcount
        print(f"   Deleted {deleted} chunks for {rel_path}")
    conn.commit()

    # Process the files
    print("\n3. Processing files...")
    total, conn, cur = process_files(conn, cur, md_files, mode="update")

    print("\n" + "=" * 60)
    print("UPDATE COMPLETE")
    print("=" * 60)
    print(f"Files updated: {len(md_files)}")
    print(f"Chunks indexed: {total}")

    cur.close()
    conn.close()

def smart_reindex():
    """Smart reindex - only re-embeds files that have changed (default behavior)"""
    print("=" * 60)
    print("SEMANTIC INDEX - SMART REINDEX")
    print("=" * 60)

    print("\n1. Connecting to Supabase...")
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        print("   Connected!")
    except Exception as e:
        print(f"   ERROR: {e}")
        return

    print("\n2. Loading existing file hashes from database...")
    existing_hashes = get_existing_hashes(cur)
    print(f"   Found {len(existing_hashes)} files with hashes in database")

    print(f"\n3. Scanning {DOCS_PATH}...")
    all_md_files = get_md_files(DOCS_PATH)
    current_file_paths = {rel_path for _, rel_path in all_md_files}
    print(f"   Found {len(all_md_files)} markdown files")

    # Check for orphaned entries (files in DB but not on disk)
    # Exclude conversation: paths - those are managed by embed_conversations.py
    print("\n4. Checking for deleted/archived files...")
    orphans = [path for path in existing_hashes.keys()
               if path not in current_file_paths and not path.startswith("conversation:")]
    if orphans:
        total_deleted = 0
        for rel_path in orphans:
            cur.execute("DELETE FROM doc_embeddings WHERE file_path = %s", (rel_path,))
            deleted = cur.rowcount
            total_deleted += deleted
            print(f"   REMOVED: {rel_path} ({deleted} chunks)")
        conn.commit()
        print(f"   Cleaned up {len(orphans)} deleted files ({total_deleted} chunks)")
    else:
        print("   No deleted files to clean up")

    print("\n5. Checking for changes...")
    files_to_update = []
    files_skipped = 0
    file_hashes = {}

    for full_path, rel_path in all_md_files:
        try:
            current_hash = get_file_hash(full_path)
            file_hashes[rel_path] = current_hash

            if rel_path in existing_hashes and existing_hashes[rel_path] == current_hash:
                files_skipped += 1
            else:
                files_to_update.append((full_path, rel_path))
                if rel_path in existing_hashes:
                    print(f"   CHANGED: {rel_path}")
                else:
                    print(f"   NEW: {rel_path}")
        except Exception as e:
            print(f"   Error checking {rel_path}: {e}")

    print(f"\n   Files unchanged (skipped): {files_skipped}")
    print(f"   Files to process: {len(files_to_update)}")

    if not files_to_update and not orphans:
        print("\n" + "=" * 60)
        print("NO CHANGES DETECTED - INDEX IS UP TO DATE")
        print("=" * 60)
        cur.close()
        conn.close()
        return

    if not files_to_update:
        # Only orphans were cleaned, no new files to process
        print("\n" + "=" * 60)
        print("SMART REINDEX COMPLETE")
        print("=" * 60)
        print(f"Files cleaned up: {len(orphans)}")
        print(f"Files skipped (unchanged): {files_skipped}")
        cur.execute("SELECT COUNT(*) FROM doc_embeddings;")
        print(f"Database total: {cur.fetchone()[0]}")
        cur.close()
        conn.close()
        return

    # Delete old embeddings for changed files
    print("\n6. Removing old embeddings for changed files...")
    for full_path, rel_path in files_to_update:
        cur.execute("DELETE FROM doc_embeddings WHERE file_path = %s", (rel_path,))
        deleted = cur.rowcount
        if deleted > 0:
            print(f"   Deleted {deleted} chunks for {rel_path}")
    conn.commit()

    # Process only the changed files
    print("\n7. Processing changed files...")
    total, conn, cur = process_files(conn, cur, files_to_update, mode="smart", file_hashes=file_hashes)

    print("\n" + "=" * 60)
    print("SMART REINDEX COMPLETE")
    print("=" * 60)
    if orphans:
        print(f"Files cleaned up: {len(orphans)}")
    print(f"Files skipped (unchanged): {files_skipped}")
    print(f"Files processed: {len(files_to_update)}")
    print(f"Chunks indexed: {total}")

    cur.execute("SELECT COUNT(*) FROM doc_embeddings;")
    print(f"Database total: {cur.fetchone()[0]}")

    cur.close()
    conn.close()

def remove_orphans():
    """Remove embeddings for files that no longer exist on disk"""
    print("=" * 60)
    print("SEMANTIC INDEX - REMOVE ORPHANS")
    print("=" * 60)

    print("\n1. Connecting to Supabase...")
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        print("   Connected!")
    except Exception as e:
        print(f"   ERROR: {e}")
        return

    print("\n2. Getting indexed file paths from database...")
    cur.execute("SELECT DISTINCT file_path FROM doc_embeddings")
    indexed_paths = [row[0] for row in cur.fetchall()]
    print(f"   Found {len(indexed_paths)} unique file paths in database")

    print("\n3. Checking for orphaned entries...")
    orphans = []
    for rel_path in indexed_paths:
        full_path = os.path.join(DOCS_PATH, rel_path)
        if not os.path.exists(full_path):
            orphans.append(rel_path)
            print(f"   ORPHAN: {rel_path}")

    if not orphans:
        print("   No orphans found - database is clean!")
        cur.close()
        conn.close()
        return

    print(f"\n4. Removing {len(orphans)} orphaned entries...")
    total_deleted = 0
    for rel_path in orphans:
        cur.execute("DELETE FROM doc_embeddings WHERE file_path = %s", (rel_path,))
        deleted = cur.rowcount
        total_deleted += deleted
        print(f"   Deleted {deleted} chunks for {rel_path}")
    conn.commit()

    print("\n" + "=" * 60)
    print("ORPHAN REMOVAL COMPLETE")
    print("=" * 60)
    print(f"Files removed: {len(orphans)}")
    print(f"Chunks deleted: {total_deleted}")

    cur.execute("SELECT COUNT(*) FROM doc_embeddings;")
    print(f"Database total: {cur.fetchone()[0]}")

    cur.close()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description='MRMINOR Semantic Index')
    parser.add_argument('--files', nargs='+', help='Specific files to update (incremental)')
    parser.add_argument('--full', action='store_true', help='Force full reindex (TRUNCATE + re-embed all)')
    parser.add_argument('--remove-orphans', action='store_true', help='Remove embeddings for deleted files')

    args = parser.parse_args()

    if args.full:
        # Explicit full reindex requested
        full_reindex()
    elif args.remove_orphans:
        # Remove orphaned embeddings
        remove_orphans()
    elif args.files:
        # Update specific files only
        update_files(args.files)
    else:
        # Default: smart reindex (only changed files)
        smart_reindex()

if __name__ == "__main__":
    main()
