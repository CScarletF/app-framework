FROM php:8.2-fpm

# libpq-dev is required to compile the pdo_pgsql extension — pdo_pgsql
# isn't in base PHP images by default, since not every PHP app needs
# Postgres specifically.
RUN apt-get update && apt-get install -y libpq-dev \
    && docker-php-ext-install pdo pdo_pgsql

# Grabs the composer binary from Composer's own official image rather
# than installing it via a shell script — this is the standard,
# reproducible way to get Composer into a Docker image.
COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

WORKDIR /var/www/backend