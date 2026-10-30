FROM python:3.12-slim
ARG APP_COMMIT_SHA=unknown
LABEL org.opencontainers.image.revision=$APP_COMMIT_SHA
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV APP_COMMIT_SHA=$APP_COMMIT_SHA
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev
COPY . .
RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /app/media /app/staticfiles \
    && chown -R app:app /app \
    && chmod +x /app/entrypoint.sh
USER app
CMD ["/app/entrypoint.sh"]
