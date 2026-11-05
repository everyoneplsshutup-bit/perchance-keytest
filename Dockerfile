# Perchance key-mint test container (SEPARATE from the live API — never deploy this to the live container).
# Runs a real headless Chromium, opens the Perchance generator page, watches
# the Turnstile gate, and if a userKey is ever minted, immediately tests it
# server-side from this same container (same IP) via the real generate API.
# Result is served as JSON at GET / — no key material is ever written anywhere.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 libatspi2.0-0 \
    fonts-liberation fonts-unifont \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt \
  && python -m playwright install chromium
COPY test_app.py .
EXPOSE 8000
CMD ["sh", "-c", "uvicorn test_app:app --host 0.0.0.0 --port ${PORT:-8000}"]
