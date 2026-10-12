FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCOUT_WORKSPACE_ROOT=/app \
    SCOUT_HOST=0.0.0.0 \
    SCOUT_PORT=8000

WORKDIR /app

COPY . /app

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e . --no-deps \
    && python -m pip install --no-cache-dir -r requirements.lock \
    && mkdir -p /app/state

EXPOSE 3000 8000

# Use Vercel's injected PORT when present; retain port 8000 for standalone container hosts.
# Public binds intentionally fail closed unless SCOUT_SERVER_TOKEN is configured.
CMD ["sh", "-c", "python -m autonomous_agent.server --host 0.0.0.0 --port \"${PORT:-${SCOUT_PORT:-8000}}\" --root /app"]
