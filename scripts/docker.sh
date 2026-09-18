docker compose -f core/docker/docker-compose.yml run --rm app python core/backend/sync_tables.py --module=recipe --apply-schema
docker compose -f core/docker/docker-compose.yml restart app