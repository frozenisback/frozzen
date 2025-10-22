# Use official Python image
FROM python:3.11-slim

# Labels / metadata
LABEL maintainer="you <you@example.com>"

# Avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000

# Install system dependencies and ffmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ffmpeg \
      build-essential \
      git \
      curl \
      ca-certificates \
      && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . /app

# Expose the port
EXPOSE $PORT

# Use non-root user for safety (optional on Heroku)
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# Run Gunicorn with gthread, 1 worker, 1 thread, long timeout
CMD ["gunicorn", "app:app", "--worker-class", "gthread", "--threads", "1", "--workers", "1", "--timeout", "500", "--bind", "0.0.0.0:$PORT"]
