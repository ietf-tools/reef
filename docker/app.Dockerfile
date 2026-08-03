FROM ghcr.io/ietf-tools/reef-app-base:latest
LABEL maintainer="IETF Tools Team <tools-discuss@ietf.org>"

ENV DEBIAN_FRONTEND=noninteractive

# Install needed packages and set up the non-root user.
ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=$USER_UID
COPY docker/scripts/app-setup.sh /tmp/library-scripts/docker-setup.sh
RUN sed -i 's/\r$//' /tmp/library-scripts/docker-setup.sh && chmod +x /tmp/library-scripts/docker-setup.sh
RUN bash /tmp/library-scripts/docker-setup.sh "${USERNAME}" "${USER_UID}" "${USER_GID}"
RUN pip3 --disable-pip-version-check --no-cache-dir install --no-warn-script-location watchdog[watchmedo]

COPY docker/configs/.tmux.conf /home/dev/.tmux.conf

# Set up nginx
COPY docker/configs/nginx-proxy.conf /etc/nginx/sites-available/default
COPY docker/configs/nginx-502.html /var/www/html/502.html

# Enable manage.py completion for bash/zsh
RUN mkdir -p /usr/local/share/django/extras
RUN wget -O /usr/local/share/django/extras/django_bash_completion \
    https://github.com/django/django/raw/main/extras/django_bash_completion
RUN echo "source /usr/local/share/django/extras/django_bash_completion" >> /home/$USERNAME/.profile

# Copy the startup scripts
COPY docker/scripts/app-init.sh /docker-init.sh
COPY docker/scripts/app-start.sh /docker-start.sh
COPY docker/scripts/celery-init.sh /celery-init.sh
RUN sed -i 's/\r$//' /docker-init.sh && chmod +x /docker-init.sh
RUN sed -i 's/\r$//' /docker-start.sh && chmod +x /docker-start.sh
RUN sed -i 's/\r$//' /celery-init.sh && chmod +x /celery-init.sh

# Fix user UID / GID to match the host
RUN groupmod --gid $USER_GID $USERNAME \
    && usermod --uid $USER_UID --gid $USER_GID $USERNAME \
    && chown -R $USER_UID:$USER_GID /home/$USERNAME \
    || exit 0

# Switch to the local dev user
USER dev:dev
