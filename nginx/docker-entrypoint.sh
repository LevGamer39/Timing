#!/bin/sh
# Подставляет домен из .env в конфиг nginx и следит за сертификатом.
#
# Домен приходит единственной переменной APP_DOMAIN, поэтому переезд на
# другой домен не требует правок ни в этом файле, ни в docker-compose.
set -eu

DOMAIN="${APP_DOMAIN:-localhost}"
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
FALLBACK_DIR="/etc/nginx/selfsigned"

REAL_CERT="${CERT_DIR}/fullchain.pem"
REAL_KEY="${CERT_DIR}/privkey.pem"

if [ -s "$REAL_CERT" ] && [ -s "$REAL_KEY" ]; then
    SSL_CERT="$REAL_CERT"
    SSL_KEY="$REAL_KEY"
    echo "[nginx] домен ${DOMAIN}, сертификат Let's Encrypt найден"
else
    # Nginx с несуществующим путём к сертификату просто не стартует, а на
    # свежей машине сертификата ещё нет. Подставляем самоподписанный:
    # сайт поднимается сразу, certbot заменит его настоящим при renew.
    SSL_CERT="${FALLBACK_DIR}/fullchain.pem"
    SSL_KEY="${FALLBACK_DIR}/privkey.pem"
    mkdir -p "$FALLBACK_DIR"
    if [ ! -s "$SSL_CERT" ] || [ ! -s "$SSL_KEY" ]; then
        echo "[nginx] сертификата для ${DOMAIN} нет, создаю самоподписанный"
        openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
            -subj "/CN=${DOMAIN}" \
            -keyout "$SSL_KEY" \
            -out "$SSL_CERT" 2>/dev/null
        echo "[nginx] выпусти настоящий сертификат командой из README"
    fi
fi

# envsubst читает окружение, а не shell-переменные: без export подстановка
# дала бы пустую строку и nginx упал бы на "invalid number of arguments".
export APP_DOMAIN SSL_CERT SSL_KEY

# Подставляем только свои переменные: envsubst с пустым списком заменил бы
# и $host, и $remote_addr, которые nginx обязан видеть как есть.
envsubst '${APP_DOMAIN} ${SSL_CERT} ${SSL_KEY}' \
    < /etc/nginx/nginx.conf.template \
    > /etc/nginx/nginx.conf

nginx -t

exec "$@"