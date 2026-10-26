FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
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
