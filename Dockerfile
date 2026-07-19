# Slim Python 3.11 — smaller image, faster builds
FROM python:3.11-slim

# All work happens in /app inside container
WORKDIR /app

# Copy requirements first — Docker caches this layer
# Only reinstalls packages if requirements.txt changes
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy rest of codebase
COPY . .

# Start FastAPI with hot reload for development
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]