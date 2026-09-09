FROM ghcr.io/ietf-tools/reef-app-base:20260909T0819
LABEL maintainer="IETF Tools Team <tools-discuss@ietf.org>"

ENV DEBIAN_FRONTEND=noninteractive

RUN groupadd -g 1000 reef && \
    useradd -c "Reef" -u 1000 -g reef -m -s /bin/false reef

COPY . .
COPY ./dev/build/start.sh ./start.sh
COPY ./dev/build/backend-start.sh ./backend-start.sh
COPY ./dev/build/celery-start.sh ./celery-start.sh
COPY ./dev/build/migration-start.sh ./migration-start.sh
COPY ./dev/build/gunicorn.conf.py ./gunicorn.conf.py

RUN pip3 --disable-pip-version-check --no-cache-dir install -r requirements.txt

# Generate and validate the OpenAPI schema at build time.
RUN REEF_DEPLOYMENT_MODE=build ./manage.py spectacular --file reef_api.yaml --validate

# Vendor the self-hosted SurveyJS bundles, then collect every static file into
# STATIC_ROOT. Both have to happen here rather than at startup: whitenoise
# serves STATIC_ROOT out of the image, and the pod's root filesystem is
# read-only, so this is the last point at which anything can write it. The two
# steps and their order mirror docker/scripts/app-init.sh, which does the same
# for the dev container -- the bundles are not committed, so collectstatic has
# nothing to find until sync.sh has run. --clear keeps a stale ./static from a
# local build context out of the image.
RUN cd vendor && npm ci && npm run sync && rm -rf node_modules
RUN REEF_DEPLOYMENT_MODE=build ./manage.py collectstatic --no-input --clear

RUN chmod +x start.sh backend-start.sh celery-start.sh migration-start.sh

CMD ["./start.sh"]

EXPOSE 8000
