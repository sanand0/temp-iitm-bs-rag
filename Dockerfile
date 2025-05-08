FROM python:3.13-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Switch to app directory
WORKDIR /app

# Copy application code
COPY main.py .

# Expose port
EXPOSE 8080

# Run with uv
CMD ["uv", "run", "main.py"]
