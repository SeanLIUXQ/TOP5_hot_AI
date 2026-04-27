FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir -e .

COPY app ./app
COPY scripts ./scripts
COPY docs ./docs
COPY .env.example ./.env.example

RUN mkdir -p /app/data /app/output/reports && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

