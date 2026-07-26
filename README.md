# AI Code Review Platform

A full-stack platform that automatically reviews GitHub pull requests using AI to catch bugs, find security flaws, and improve code quality.

## Tech Stack
* Backend: FastAPI (Python)
* Database: PostgreSQL (with SQLAlchemy)
* Queue & Cache: Celery & Redis (for handling heavy background tasks)
* AI Engines: Groq & Anthropic (via LangGraph workflows)

## Production Notes
This project is designed to run efficiently on lightweight servers (like an AWS EC2 t3.micro). 

To save disk space and memory, the platform uses external AI APIs rather than downloading massive machine learning libraries locally. While the codebase supports local embeddings (sentence-transformers) and vector search (FAISS), these features are turned off by default in production to keep the server lean and fast.

## Quick Setup

### 1. Set up your environment
Create a .env file in the root folder with your credentials:

DATABASE_URL=postgresql+asyncpg://user:password@db:5432/codereview
REDIS_URL=redis://redis:6379/0
GITHUB_TOKEN=your_github_token
GROQ_API_KEY=your_groq_api_key
ANTHROPIC_API_KEY=your_anthropic_api_key

### 2. Run with Docker
Start the entire platform (backend, database, and background workers) with one command:

docker compose up -d --build

Once running, you can open your browser and go to http://localhost:8000/docs to see the interactive API dashboard.