# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "asyncpg",
#     "fastapi",
#     "openai",
#     "pydantic",
#     "python-dotenv",
#     "uvicorn",
# ]
# ///

# main.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional
import asyncio
import asyncpg
import os
import uuid
from dotenv import load_dotenv
from openai import AsyncOpenAI
from contextlib import asynccontextmanager

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: create the pool
    app.state.pool = await asyncpg.create_pool(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        max_size=DB_POOL_SIZE,
    )

    # tell asyncpg how to serialize & deserialize `vector` in text format
    async with app.state.pool.acquire() as conn:
        await conn.set_type_codec(
            "vector",
            # encoder: Python list[float] → "[f1,f2,...]" text
            encoder=lambda vec: "[" + ",".join(map(str, vec)) + "]",
            # decoder: "[f1,f2,...]" → Python list[float]
            decoder=lambda s: [float(x) for x in s.strip("[]").split(",") if x],
            schema="public",
            format="text",
        )

    yield
    # shutdown: close it
    if app.state.pool:
        await app.state.pool.close()


app = FastAPI(title="Hybrid RAG API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration from environment variables
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
DB_USER = "postgres"
DB_PASS = os.getenv("PASSWORD")
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "32"))

# OpenAI API configuration
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4.1-micro")


class Chunk(BaseModel):
    id: Optional[str] = None
    content: str


class ChunkList(BaseModel):
    chunks: List[Chunk]


class Query(BaseModel):
    q: str
    count: int = Field(5, ge=1, le=20)
    text_weight: float = Field(0.7, ge=0, le=1.0)
    vector_weight: float = Field(0.3, ge=0, le=1.0)


class Answer(BaseModel):
    query: str
    answer: str
    sources: List[str]


@app.post("/chunks", status_code=201)
async def add_chunks(chunks: ChunkList, request: Request):
    if not request.app.state.pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    # Generate embeddings for all chunks in one API call
    contents = [chunk.content for chunk in chunks.chunks]
    embedding_response = await openai_client.embeddings.create(
        model=EMBEDDING_MODEL, input=contents
    )

    # Batch insert (id, content, embedding) tuples
    async with request.app.state.pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                "INSERT INTO chunks (id, content, embedding) VALUES ($1, $2, $3)",
                [
                    (
                        chunk.id or str(uuid.uuid4()),
                        chunk.content,
                        embedding_response.data[i].embedding,
                    )
                    for i, chunk in enumerate(chunks.chunks)
                ],
            )

    return {"message": f"Added {len(chunks.chunks)} chunks"}


@app.post("/search")
async def search(query: Query, request: Request):
    if not request.app.state.pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    # Generate embedding for query
    embedding_response = await openai_client.embeddings.create(
        model=EMBEDDING_MODEL, input=query.q
    )
    query_embedding = embedding_response.data[0].embedding

    # Format query for text search
    async with request.app.state.pool.acquire() as conn:
        query_terms = await conn.fetchval("SELECT prepare_query_terms($1)", query.q)

        # Execute hybrid search
        results = await conn.fetch(
            "SELECT * FROM hybrid_search($1, $2, $3, $4, $5)",
            query_terms,
            query_embedding,
            query.count,
            query.text_weight,
            query.vector_weight,
        )

    return {"results": [dict(r) for r in results]}


@app.post("/answer", response_model=Answer)
async def answer_query(query: Query):
    # Search for relevant chunks
    search_results = await search(query)

    # Extract chunk contents
    chunks = [r["content"] for r in search_results["results"]]

    if not chunks:
        return Answer(query=query.q, answer="No relevant information found.", sources=[])

    # Generate answer with LLM
    prompt = f"Question: {query.q}\n\nContext:\n" + "\n---\n".join(chunks)
    system_prompt = "Answer the question based ONLY on the provided context. Cite specific phrases from the context."

    completion = await openai_client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    )

    return Answer(query=query.q, answer=completion.choices[0].message.content, sources=chunks)


# Health check endpoint
@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
