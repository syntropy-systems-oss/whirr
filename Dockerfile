# Dockerfile for whirr server
#
# Build:  docker build -t whirr .
# Run:    docker run -p 8080:8080 -e WHIRR_DATABASE_URL=... whirr

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy package files
COPY pyproject.toml .
COPY src/ src/

# Install whirr with server dependencies
RUN pip install --no-cache-dir -e ".[server]"

# Create data directory
RUN mkdir -p /data/runs

# Expose port
EXPOSE 8080

# Default command
CMD ["whirr", "server", "--host", "0.0.0.0", "--port", "8080"]
