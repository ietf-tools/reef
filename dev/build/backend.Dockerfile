FROM ghcr.io/ietf-tools/pink-app-base:latest
LABEL maintainer="IETF Tools Team <tools-discuss@ietf.org>"

ENV DEBIAN_FRONTEND=noninteractive

RUN groupadd -g 1000 pink && \
    useradd -c "Pink" -u 1000 -g pink -m -s /bin/false pink

COPY . .
COPY ./dev/build/start.sh ./start.sh
COPY ./dev/build/backend-start.sh ./backend-start.sh
COPY ./dev/build/celery-start.sh ./celery-start.sh
COPY ./dev/build/migration-start.sh ./migration-start.sh
COPY ./dev/build/gunicorn.conf.py ./gunicorn.conf.py

RUN pip3 --disable-pip-version-check --no-cache-dir install -r requirements.txt

# Generate and validate the OpenAPI schema at build time.
RUN PINK_DEPLOYMENT_MODE=build ./manage.py spectacular --file pink_api.yaml --validate

RUN chmod +x start.sh backend-start.sh celery-start.sh migration-start.sh

CMD ["./start.sh"]

EXPOSE 8000
