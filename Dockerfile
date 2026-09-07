# ==============================================================================
# Nemo: Pump.fun Real-Time Forensics & Quantitative Research HUD
# Optimized Dockerfile for 24/7 Deployment (Oracle Cloud ARM / x86_64)
# ==============================================================================

FROM python:3.11-slim

# Prevent Python from buffering stdout/stderr and creating .pyc files
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system runtime dependencies for DuckDB, LightGBM, and network tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for caching layers
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir uvicorn fastapi httpx

# Copy project source code
COPY . .

# Ensure data and config directories exist
RUN mkdir -p data config

# Expose Web Dashboard and WebSocket port
EXPOSE 8000

# Health check to ensure dashboard server is responding
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/stats || exit 1

# Launch production dashboard server with automated ingestion and background cohort auditor
CMD ["python3", "-m", "uvicorn", "src.dashboard.server:app", "--host", "0.0.0.0", "--port", "8000"]
