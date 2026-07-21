# ad-decision-service: the serving path — targeting, brand safety, capping, pacing.
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Dependencies first so image layers cache across source changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY substrate ./substrate

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "substrate.ad_decision_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
