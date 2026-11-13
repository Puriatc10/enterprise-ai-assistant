# Build context is the REPO ROOT (see docker-compose.yml), so paths below start
# at the repository, not at apps/api.
# (No `# syntax=` directive: it makes BuildKit pull a frontend image from Docker
# Hub before anything else, an avoidable dependency on registry availability.)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependency layer first: copy only what the build needs.
COPY apps/api/pyproject.toml ./pyproject.toml
COPY apps/api/app ./app

RUN pip install --no-cache-dir .

# Run as a non-root user; the container keeps no writable state by design
# (persistence is Supabase, logs go to stdout).
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
