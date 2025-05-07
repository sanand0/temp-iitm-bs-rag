# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "aiohttp",
#     "python-dotenv",
# ]
# ///

# upload_chunks.py
import asyncio
import aiohttp
import json
import os
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv("API_URL")  # Your Cloud Run service URL
BATCH_SIZE = 10  # Number of chunks to send in each request

async def upload_chunks(chunks):
    async with aiohttp.ClientSession() as session:
        # Process in batches
        for i in range(0, len(chunks), BATCH_SIZE):
            # Send batch to API
            payload = {"chunks": chunks[i:i + BATCH_SIZE]}
            async with session.post(f"{API_URL}/chunks", json=payload) as response:
                print(payload)
                if response.status != 201:
                    print(f"Error uploading batch {i//BATCH_SIZE}: {await response.text()}")
                else:
                    print(f"Uploaded batch {i//BATCH_SIZE + 1}/{(len(chunks) + BATCH_SIZE - 1)//BATCH_SIZE}")

            # Slight delay to avoid overwhelming the API
            await asyncio.sleep(0.5)

async def main():
    # Load chunks from chunks.json
    with open("chunks.json", "r") as f:
        chunks = [json.loads(line) for line in f.readlines()]

    print(f"Uploading {len(chunks)} chunks to {API_URL}")
    await upload_chunks(chunks)
    print("Upload complete!")

if __name__ == "__main__":
    asyncio.run(main())
