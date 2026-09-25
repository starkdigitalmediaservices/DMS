# Full Project Setup & Installation Guide

This guide provides complete instructions for deploying the Multi-Tenant AI Document Management & Search Platform using either **Docker Compose (One-Click Deployment)** or **Local Native Development Setup**.

---

## 1. Prerequisites

Before installing, ensure your machine has the following tools installed:

| Tool | Minimum Version | Recommended Version |
|------|-----------------|---------------------|
| **Docker & Docker Compose** | Docker v24.0+ | Docker Desktop 4.25+ / Docker Engine 25+ |
| **Python** | 3.12 | 3.12.3+ |
| **Node.js** | 18.x LTS | Node.js 20 LTS |
| **PostgreSQL** | 16 with `pgvector` | `ankane/pgvector:latest` image |
| **Redis** | 7.0+ | Redis Alpine image |

---

## 2. Option 1: One-Click Docker Setup (Recommended)

The easiest way to launch all 6 system containers (PostgreSQL + pgvector, Redis, MinIO, FastAPI Backend, Celery Worker, Flower, and Next.js Frontend) is using Docker Compose.

```bash
# 1. Clone the repository
git clone <your-repository-url>
cd DMS

# 2. Configure Environment Variables
cp backend/.env.example backend/.env

# Edit backend/.env if you want to add external API keys (OpenAI, Anthropic, Groq, Cohere)
# Default values will work locally out of the box with mock settings.

# 3. Build and Launch Containers
docker compose up --build -d
```

### Accessing Running Services

Once containers are healthy, access the interfaces:

| Service | Interface | URL | Default Credentials |
|---------|-----------|-----|---------------------|
| **Web Frontend** | Next.js App UI | `http://localhost:3000` | User: `admin@example.com` / `changeme` |
| **FastAPI Backend** | Swagger API Docs | `http://localhost:8000/api/docs` | N/A |
| **Flower Dashboard** | Celery Worker Telemetry | `http://localhost:5555` | N/A |
| **MinIO Console** | S3 Administration | `http://localhost:9001` | User: `minioadmin` / `minioadmin` |

---

## 3. Option 2: Native Local Development Setup

If you prefer to run Python, Celery, and Next.js natively on your host OS while running PostgreSQL, Redis, and MinIO in background containers, follow these steps:

### Step 3.1: Start Infrastructure Containers
Use the included shell helper script or run minimal containers:

```bash
# Option A: Run helper script
chmod +x scripts/setup_postgres_redis.sh
./scripts/setup_postgres_redis.sh

# Option B: Run containers directly
docker run -d --name dms-postgres -p 5432:5432 -e POSTGRES_DB=docsearch -e POSTGRES_USER=docsearch -e POSTGRES_PASSWORD=docsearch ankane/pgvector:latest
docker run -d --name dms-redis -p 6379:6379 redis:7-alpine redis-server --requirepass redispassword
docker run -d --name dms-minio -p 9000:9000 -p 9001:9001 -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin minio/minio server /data --console-address ":9001"
```

### Step 3.2: Configure Python Virtual Environment
```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate

# Upgrade pip and install all backend requirements
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3.3: Configure Environment Variables (`backend/.env`)
Copy `.env.example` to `.env` and fill in local database connection strings:

```env
POSTGRES_URL=postgresql+asyncpg://docsearch:docsearch@localhost:5432/docsearch
REDIS_URL=redis://:redispassword@localhost:6379/0

JWT_SECRET_KEY=super-secret-key-change-in-production-12345
JWT_ALGORITHM=HS256

AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
AWS_REGION=us-east-1
S3_BUCKET_NAME=docsearch-documents
S3_ENDPOINT_URL=http://localhost:9000
S3_PUBLIC_ENDPOINT_URL=http://localhost:9000

AI_LLM_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_LLM_MODEL=gpt-4o-mini
OPENAI_EMBED_MODEL=text-embedding-3-small

COHERE_API_KEY=your_cohere_api_key_here
```

### Step 3.4: Apply Database Migrations & Seed Default Data
```bash
# In backend/ directory with virtualenv active:

# 1. Run Alembic Database Migrations
alembic upgrade head

# 2. Seed Default Tenant and Super-Admin Account
python -m app.init_db
```

Output should confirm:
`[OK] Default Tenant and Admin User (admin@example.com) created successfully.`

### Step 3.5: Start FastAPI Backend Server
```bash
# Terminal 1: Backend API
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Step 3.6: Start Celery Background Worker
```bash
# Terminal 2: Celery Worker Process
cd backend
source .venv/bin/activate
celery -A app.tasks.worker.celery_app worker --loglevel=info
```

### Step 3.7: Start Flower Task Dashboard (Optional)
```bash
# Terminal 3: Flower Telemetry UI
cd backend
source .venv/bin/activate
celery -A app.tasks.worker.celery_app flower --port=5555
```

### Step 3.8: Start Next.js Frontend Application
```bash
# Terminal 4: Next.js Frontend
cd frontend
npm install
npm run dev
```

The application will be live at `http://localhost:3000`.

---

## 4. Environment Variables Reference Table

| Variable | Description | Default / Example |
|----------|-------------|-------------------|
| `POSTGRES_URL` | Async PostgreSQL Connection String | `postgresql+asyncpg://docsearch:docsearch@postgres:5432/docsearch` |
| `REDIS_URL` | Redis URL with Password | `redis://:redispassword@redis:6379/0` |
| `JWT_SECRET_KEY` | Secret Key for Signing JWT Tokens | `min-32-character-secret-key` |
| `S3_BUCKET_NAME` | Bucket name in S3 / MinIO | `docsearch-documents` |
| `S3_ENDPOINT_URL` | Internal S3 Service Endpoint | `http://minio:9000` (Docker) or `http://localhost:9000` |
| `AI_LLM_PROVIDER` | Active LLM Provider (`openai` / `anthropic` / `groq`) | `openai` |
| `AI_EMBED_PROVIDER` | Active Vector Embedding Provider | `openai` |
| `AI_RERANK_PROVIDER` | Active Re-Ranking Engine (`cohere` / `none`) | `cohere` |
| `RATE_LIMIT_PER_USER` | SlowAPI User Rate Limit | `60/minute` |

---

## 5. Verification & Health Check Commands

Run these quick sanity commands to verify that all sub-systems are operating cleanly:

### 1. Test PostgreSQL Vector Extension
```bash
docker exec -it dms-postgres psql -U docsearch -d docsearch -c "SELECT * FROM pg_extension WHERE extname = 'vector';"
```

### 2. Verify Redis Connectivity
```bash
docker exec -it dms-redis redis-cli -a redispassword ping
# Output: PONG
```

### 3. Verify Backend API Health & Open Docs
Open `http://localhost:8000/api/docs` in browser or run curl:
```bash
curl http://localhost:8000/api/docs
```
