# Perchance key-mint test container (SEPARATE from the live API — never deploy this to the live container).
# Runs a real headless Chromium, opens the Perchance generator page, watches
# the Turnstile gate, and if a userKey is ever minted, immediately tests it
# server-side from this same container (same IP) via the real generate API.
# Result is served as JSON at GET / — no key material is ever written anywhere.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install chromium --with-deps
COPY test_app.py .
EXPOSE 8000
CMD ["sh", "-c", "uvicorn test_app:app --host 0.0.0.0 --port ${PORT:-8000}"]
