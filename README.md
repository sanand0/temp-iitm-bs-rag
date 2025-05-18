# Set up environment variables

First, copy the environment variables setup and edit the environment variables.

```bash
cp sample.env .env
```

and load the environment variables

```bash
source .env
```

# Crawl the website

Download the content using [wget](https://www.gnu.org/software/wget/):

```bash
wget \
  --recursive \
  --level=$CRAWL_LEVEL \
  --no-parent \
  --convert-links \
  --adjust-extension \
  --compression=auto \
  --accept html,htm \
  --directory-prefix=./website \
  $WEBSITE
```

# Chunk the content

Create `.md` files for each `.html` file. This requires [npx](https://nodejs.org/)

```bash
find content/ -name '*.html' -exec npx --package defuddle-cli -y defuddle parse {} --md -o {}.md \;
```

Split Markdown files into chunks. This requires [jq](https://jqlang.org/) and [uv](https://docs.astral.sh/uv/):

```bash
(
  shopt -s globstar
  for f in content/**/*.md; do
    uvx --from split_markdown4gpt mdsplit4gpt "$f" --model gpt-4o --limit 4096 --separator "===SPLIT===" \
    | sed '1s/^/===SPLIT===\n/' \
    | jq -R -s -c --arg file "$f" '
      split("===SPLIT===")[1:]
      | to_entries
      | map({
          id: ($file + "#" + (.key | tostring)),
          content: .value
        })[]
    '
  done
) | tee chunks.json
```

# Set up Google Cloud SQL

Install Google Cloud CLI and log in:

```bash
curl https://sdk.cloud.google.com | bash
gcloud auth login   # e.g. anand@study.iitm.ac.in
gcloud config set project $PROJECT
```

Create a PostgreSQL instance with the `pg_vector` extension:

```bash
gcloud sql instances create $INSTANCE --region=$REGION \
  --database-version=POSTGRES_15 --cpu=2 --memory=4GB
gcloud sql users set-password postgres \
  --instance=$INSTANCE --password=$PASSWORD
gcloud sql databases create $DB --instance=$INSTANCE

# Get the public IP address
export DB_HOST=$(gcloud sql instances describe $INSTANCE --project=$PROJECT --format="get(ipAddresses[0].ipAddress)")
export INSTANCE_CONNECTION_NAME=$(gcloud sql instances describe iitm-bs-rag --format="value(connectionName)")

# Allow access from any IP -- use with caution
gcloud sql instances patch $INSTANCE --project=$PROJECT --authorized-networks="0.0.0.0/0"
```

Connect to the database to enable the `pg_vector` extension and create schema:

```bash
gcloud auth login  # if required
gcloud sql connect $INSTANCE --user=postgres
```

Once connected, run this SQL:

```sql
\c $DB

-- Enable vector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Create table for document chunks
CREATE TABLE chunks (
  id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
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
  id TEXT,
  content TEXT,
  similarity FLOAT
) AS $$
BEGIN
  RETURN QUERY
  SELECT
    c.id,
    c.content,
    (text_weight * ts_rank(c.ts_content, to_tsquery('english', query_text)))
    + (vector_weight * (1 - (c.embedding <=> query_embedding))) AS similarity
  FROM chunks c
  WHERE
    c.ts_content @@ to_tsquery('english', query_text)
    OR (c.embedding <=> query_embedding) < 0.4
  ORDER BY similarity DESC
  LIMIT match_count;
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
```

- **`vector` extension**: enables storage and search of embeddings
- **`chunks` table**: stores document chunks with text and embeddings
- **Generated column**: automatically creates text search vectors
- **Indexes**: optimizes both vector and text search
- **`hybrid_search` function**: combines text and vector search with weighted scoring
- **`prepare_query_terms` function**: helps format text for full-text search

### Set up FastAPI application

Test by running [main.py](main.py):

```bash
uv run main.py
```

... and visiting <http://localhost:8080/health> which should show:

```json
{ "status": "healthy" }
```

Test by uploading chunks via the local server using:

```bash
API_URL=http://localhost:8080 uv run upload_chunks.py
```

... and checking with a query:

```bash
curl -X POST http://localhost:8080/search \
  -H "Content-Type: application/json" \
  -d '{"q": "... some search string ..."}'
```

### Deploy to Google Cloud Run

Build and deploy the application:

```bash
# Create an Artifact Registry repo (one-time)
gcloud artifacts repositories create $REPOSITORY --location=$REGION \
  --repository-format=docker \
  --description="Docker images for RAG API $INSTANCE - $DB_NAME"

# Configure Docker to auth with Artifact Registry (one-time)
gcloud auth configure-docker $REGION-docker.pkg.dev

# Build & push your image
gcloud builds submit --tag $REGION-docker.pkg.dev/$PROJECT/$REPOSITORY/$INSTANCE:latest

# Deploy from Artifact Registry
gcloud run deploy rag-api \
  --image $REGION-docker.pkg.dev/$PROJECT/$REPOSITORY/$INSTANCE:latest \
  --region $REGION \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars="DB_HOST=$DB_HOST,DB_NAME=$DB_NAME,PASSWORD=$PASSWORD,OPENAI_API_KEY=$OPENAI_API_KEY"
```

Now test the application:

```bash
export SERVER=$(gcloud run services describe rag-api --region $REGION --format='value(status.url)')

# Check health
curl $SERVER/health

# Check search
curl $SERVER/search -H "Content-Type: application/json" -d '{"q": "... some search string ..."}'
```

## Run test cases

```bash
# Run tests repeatedly
npx -y promptfoo eval --repeat 5
```
