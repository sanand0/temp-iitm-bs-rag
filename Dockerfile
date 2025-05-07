FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir uvicorn fastapi uvicorn[standard] openai asyncpg pydantic

# Copy application code
COPY main.py .

# Expose port
EXPOSE 8080

# Run with uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
