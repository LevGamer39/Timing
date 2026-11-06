FROM nginx:alpine

# openssl нужен entrypoint'у, чтобы при первом запуске без сертификата
# nginx всё равно поднялся (в образе nginx:alpine его нет).
RUN apk add --no-cache openssl

COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY docker-entrypoint.sh /usr/local/bin/timing-nginx-entrypoint.sh
RUN chmod +x /usr/local/bin/timing-nginx-entrypoint.sh

EXPOSE 80 443

ENTRYPOINT ["/usr/local/bin/timing-nginx-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]