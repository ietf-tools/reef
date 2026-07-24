FROM ghcr.io/ietf-tools/pink-backend:latest AS builder

# Vendor the self-hosted SurveyJS bundles, then collect all static files.
RUN cd vendor && npm install && npm run sync
RUN PINK_DEPLOYMENT_MODE=build ./manage.py collectstatic --no-input

FROM ghcr.io/nginxinc/nginx-unprivileged:1.27
LABEL maintainer="IETF Tools Team <tools-discuss@ietf.org>"

COPY --from=builder /workspace/static /usr/share/nginx/html/static/

# Listen on 8042 instead of 8080.
RUN sed --in-place 's/8080/8042/' /etc/nginx/conf.d/default.conf
