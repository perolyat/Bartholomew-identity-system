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

# Run through the canonical serve path
#
# The container posture is decided (2026-08-27, S8): **authenticated, TLS on
# the socket, published to host loopback only, with a provisioned account and
# an explicit runtime binding.** There is deliberately no way for an operator
# to assert "this container is really only reachable locally, so relax" --
# a topology assertion is indistinguishable, from inside this process, from a
# genuinely exposed deployment, and would become the bypass that survives
# into Alpha.
#
# A container must bind 0.0.0.0 *inside its own network namespace* or
# published ports cannot reach it at all. That is not the same as being
# LAN-exposed, but this process cannot verify the difference, so it treats
# the bind as exposed and requires the full posture:
#
#   1. Publish to host loopback: `-p 127.0.0.1:5173:5173` (docker-compose.yml
#      does this). A bare `-p 5173:5173` publishes on every host interface.
#   2. BARTH_API_ALLOW_NON_LOOPBACK=1, set below, because the request boundary
#      sees the Docker bridge address rather than loopback. This now also
#      forces authentication and TLS on -- neither can be disabled while it
#      is in effect.
#   3. TLS material at BARTH_API_TLS_CERTFILE / BARTH_API_TLS_KEYFILE. Mount
#      it in; the image ships no key. Startup refuses without it.
#   4. A provisioned account (`bartholomew accounts create`) and
#      BARTH_RUNTIME_USER_ID naming it, plus BARTH_DB_PATH and
#      BARTHO_MEMORY_KEYRING_SERVICE matching that user. Startup verifies the
#      agreement and refuses to serve one identity from another's persistence.
#
# See docs/S8_ALPHA_OPERATOR_GUIDE.md for the provisioning steps.
#
# Launched via `app.serve()` rather than the `uvicorn` CLI: serve() is what
# puts TLS on the socket and runs the exposure checks before binding. A bare
# `uvicorn app:app --host 0.0.0.0` bypasses both, and the request boundary
# then refuses every plaintext request rather than failing open.
ENV BARTH_API_ALLOW_NON_LOOPBACK=1
ENV BARTH_API_HOST=0.0.0.0
CMD ["python", "-c", "import app; app.serve()"]
