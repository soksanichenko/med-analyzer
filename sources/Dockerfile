# Stage 1: Build Python venv
# The venv lives at /app/venv in both stages: venv scripts embed absolute paths.
FROM almalinux:10 AS python-builder
RUN dnf install -y python3 python3-devel gcc && dnf clean all
WORKDIR /app
COPY requirements.txt requirements.txt
RUN python3 -m venv venv && \
    venv/bin/pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime
FROM almalinux:10
RUN dnf install -y python3 && dnf clean all
RUN groupadd --gid 1000 medanalyzer && \
    useradd --uid 1000 --gid medanalyzer --no-create-home --shell /sbin/nologin \
        medanalyzer && \
    mkdir /data && chown medanalyzer:medanalyzer /data && chmod 0700 /data
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STORAGE_DIR=/data
# alembic.ini's script_location is relative to the working directory.
WORKDIR /app
# Application files stay owned by root: the app user can read but not modify
# them; /data (the volume with uploaded PDFs) is the only writable path.
COPY --from=python-builder /app/venv ./venv
COPY src ./src
COPY alembic.ini entrypoint.sh ./
USER medanalyzer
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/app/venv/bin/python", "-c", \
         "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
ENTRYPOINT ["sh", "/app/entrypoint.sh"]
