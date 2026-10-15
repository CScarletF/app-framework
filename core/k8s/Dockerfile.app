# core/k8s/Dockerfile.app -- application image for Kubernetes.
#
# Build context: repository root (deploy_k8s.py build runs it for you).
# Unlike core/docker/python/Dockerfile, where code is bind-mounted at
# runtime, this image CONTAINS the code: it is immutable and tagged per
# commit. It never contains .env (excluded by Dockerfile.app.dockerignore);
# configuration arrives as environment variables from the cluster's
# ConfigMap and Secret.
#
# modules/<name>/table_core.py must be committed before building: the
# generated table objects are baked in, nothing is reflected at runtime.

FROM python:3.12-slim

ARG SOURCE_URL=""
LABEL org.opencontainers.image.source="${SOURCE_URL}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# psycopg2-binary ships its own libpq, so no compiler or libpq-dev here.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY core/backend /app/core/backend
COPY modules /app/modules
COPY permissions.conf /app/permissions.conf

# Numeric UID: Kubernetes runAsNonRoot can only verify numeric users.
USER 10001:10001

EXPOSE 8000

# Worker count and tmp dir come from GUNICORN_CMD_ARGS (set in the
# Deployment), so they can change without rebuilding.
CMD ["gunicorn", "--chdir", "core/backend", "-b", "0.0.0.0:8000", "app:app"]
