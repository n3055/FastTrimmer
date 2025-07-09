FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y ffmpeg curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy files
COPY . /app

# Install Python dependencies
RUN pip install --no-cache-dir fastapi uvicorn pandas numpy gdown

# Expose FastAPI default port
EXPOSE 8000

# Run FastAPI + Cloudflared tunnel
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
