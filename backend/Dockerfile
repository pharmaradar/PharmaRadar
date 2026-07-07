FROM python:3.12-slim

# WeasyPrint + Node (for TinyFish CLI)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libcairo2 \
    libgdk-pixbuf-xlib-2.0-0 libgdk-pixbuf2.0-bin \
    libffi-dev shared-mime-info fonts-liberation \
    nodejs npm curl \
    && rm -rf /var/lib/apt/lists/*

# TinyFish CLI
RUN npm install -g @tiny-fish/cli@0.1.5

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[test]"

COPY . .

RUN mkdir -p /app/reports

# Railway injects $PORT at runtime — default to 8009 for local dev
ENV PORT=8009

# Run migrations then start the server
# CMD is overridden per-service in Railway for workers/beat
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
