# Python 3.12 slim base with timezone support
FROM python:3.12-slim

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Australia/Brisbane

# Install system dependencies including tzdata for timezone support
RUN apt-get update && \
    apt-get install -y --no-install-recommends tzdata && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory for SQLite databases
RUN mkdir -p /app/data

# Expose port
EXPOSE 5173

# Run uvicorn
# The API has no authentication and is loopback-only by default
# (DECISIONS.md, INTERFACES.md). A container must bind 0.0.0.0 *inside its own
# network namespace* or published ports cannot reach it at all -- that is not
# the same as being LAN-exposed, and it is not sufficient on its own.
#
# Two things keep it safe, and both are required:
#   1. Publish to loopback on the host: `-p 127.0.0.1:5173:5173`
#      (docker-compose.yml does this). A bare `-p 5173:5173` publishes on
#      every host interface and must not be used.
#   2. BARTH_API_ALLOW_NON_LOOPBACK=1, set deliberately below, because the
#      request boundary sees the Docker bridge address rather than loopback.
#      This prints a conspicuous warning at every startup.
ENV BARTH_API_ALLOW_NON_LOOPBACK=1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5173"]
