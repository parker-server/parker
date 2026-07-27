FROM python:3.11-slim

ARG PARKER_BUILD_COMMIT=
ENV PARKER_BUILD_COMMIT=${PARKER_BUILD_COMMIT}
LABEL org.opencontainers.image.revision="${PARKER_BUILD_COMMIT}"

# Set working directory
WORKDIR /app

USER root

# Enable non-free repositories to install 'unrar' (Required for CBR support)
# Debian Bookworm (current stable) uses sources.list.d
RUN sed -i -e's/ main/ main contrib non-free non-free-firmware/g' /etc/apt/sources.list.d/debian.sources

# Install system dependencies
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    curl \
    libmagic1 \
    unrar \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]

