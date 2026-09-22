# Build the dependencies in one stage so the runtime image carries no compilers.
FROM python:3.13-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt requirements-production.txt requirements.lock.txt ./
RUN pip install -r requirements-production.txt

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH" DJANGO_SETTINGS_MODULE=config.settings
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app . .
# Static files are baked in; media and logs are volumes the container writes to.
RUN mkdir -p /app/media /app/logs && chown -R app:app /app/media /app/logs
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz/ || exit 1
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["serve"]
