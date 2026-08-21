FROM python:3.12-slim

# libpq-dev + gcc are needed to build psycopg2 from source on some
# platforms; harmless to keep even though psycopg2-binary usually ships
# a prebuilt wheel, since a mismatched platform would otherwise fail
# silently at pip-install time.
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Only requirements.txt is copied at build time -- app code itself is
# bind-mounted at runtime (see docker-compose.yml), same approach the
# PHP version used, so code changes don't require a rebuild.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
