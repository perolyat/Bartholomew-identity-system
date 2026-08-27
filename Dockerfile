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

# Run Bartholomew as a service.
#
# The API has no authentication *of its own* -- authentication is the
# platform's (bartholomew/platform), and whether it is enforced is decided by
# the exposure rules, not by this file.
#
# A container must bind 0.0.0.0 *inside its own network namespace* or
# published ports cannot reach it at all. That is not the same as being
# LAN-exposed, and it is not sufficient on its own.
#
# Three things keep this safe, and all three are required:
#   1. Publish to loopback on the host: `-p 127.0.0.1:5173:5173`
#      (docker-compose.yml does this). A bare `-p 5173:5173` publishes on
#      every host interface and must not be used.
#   2. BARTH_API_ALLOW_NON_LOOPBACK=1, set deliberately below, because the
#      request boundary sees the Docker bridge address rather than loopback.
#      Under S8 this same variable forces authentication AND TLS on, and
#      nothing can turn either off while it is set.
#   3. TLS material and the runtime binding, supplied by docker-compose.yml.
#      Without them the process refuses to start -- which is the point: an
#      unauthenticated or plaintext container is not a configuration this
#      image can be talked into.
ENV BARTH_API_ALLOW_NON_LOOPBACK=1
# `serve` resolves its bind address through the access boundary rather than
# taking a --host flag, so the container's namespace-local 0.0.0.0 bind is
# expressed here.
ENV BARTH_API_HOST=0.0.0.0
# One process, no reload, no extra workers -- `serve` refuses those outright
# because the kernel's persistence is single-writer and it takes an exclusive
# process lock on the database at startup.
CMD ["python", "-m", "bartholomew", "serve"]
