# Flight Hacker Scraper Container
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    curl \
    openvpn \
    procps \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir \
    playwright \
    requests \
    python-dotenv

# Install Playwright browsers
RUN playwright install chromium \
    && playwright install-deps chromium

# Create app directory
WORKDIR /app

# Copy scraper code
COPY scrapers/ /app/scrapers/
COPY flight_tracker.py /app/
COPY vpn_configs/ /app/vpn_configs/

# Copy entrypoint script
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV VPN_COUNTRY=""
ENV ORIGIN="LHR"
ENV DESTINATION="BOM"
ENV DEPARTURE_DATE=""
ENV RETURN_DATE=""

ENTRYPOINT ["/app/docker-entrypoint.sh"]
