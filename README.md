# Setup instructions

First, set up variables and install the Google Cloud CLI and authenticate:


```bash
# export IITM_BS_WEBSITE_PASSWORD=...
# export PROJECT=...
export DB=iitm-bs-rag
export TABLE=iitm-bs-website

# Install & login
curl https://sdk.cloud.google.com | bash
gcloud auth login   # e.g. anand@study.iitm.ac.in
gcloud config set project $PROJECT
```

Create a PostgreSQL instance with the `pg_vector` extension:

```bash
gcloud sql instances create $DB \
  --database-version=POSTGRES_15 \
  --cpu=2 \
  --memory=4GB \
  --region=asia-south1
gcloud sql users set-password postgres \
  --instance=$DB \
  --password=$IITM_BS_WEBSITE_PASSWORD
gcloud sql databases create $TABLE \
  --instance=$DB
```

Connect to the database to enable the `pg_vector` extension and create schema:

```bash
gcloud auth login
gcloud sql connect $DB --user=postgres
```

Once connected, run this SQL:

```sql
\c $TABLE

-- Enable vector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Create table for document chunks
CREATE TABLE chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  content TEXT NOT NULL,
  embedding VECTOR(1536),
  ts_content TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);

CREATE INDEX chunks_embedding_idx ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX chunks_ts_idx ON chunks USING GIN (ts_content);

-- Create hybrid search function
CREATE OR REPLACE FUNCTION hybrid_search(
  query_text TEXT,
  query_embedding VECTOR(1536),
  match_count INT DEFAULT 5,
  text_weight FLOAT DEFAULT 0.7,
  vector_weight FLOAT DEFAULT 0.3
) RETURNS TABLE (
  id UUID,
  content TEXT,
  similarity FLOAT
) AS $$
BEGIN
  RETURN QUERY
  SELECT
    c.id,
    c.content,
    (text_weight * ts_rank(c.ts_content, to_tsquery('english', query_text))) +
    (vector_weight * (1 - (c.embedding <=> query_embedding))) AS similarity
  FROM
    chunks c
  WHERE
    c.ts_content @@ to_tsquery('english', query_text) OR
    (c.embedding <=> query_embedding) < 0.4
  ORDER BY
    similarity DESC
  LIMIT
    match_count;
END;
$$ LANGUAGE plpgsql;

-- Add example query helper function
CREATE OR REPLACE FUNCTION prepare_query_terms(query_text TEXT)
RETURNS TEXT AS $$
BEGIN
  RETURN array_to_string(
    array(
      SELECT regexp_replace(lexeme, '^\d+(.*)$', '\1')
      FROM unnest(to_tsvector('english', query_text)) AS lexeme
    ),
    ' & '
  );
END;
$$ LANGUAGE plpgsql;
EOF
```
