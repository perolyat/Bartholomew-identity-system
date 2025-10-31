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
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5173"]
