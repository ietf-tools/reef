#!/bin/bash
#
# Post-create initialization for the app container.
#

cd /workspace

# Add /workspace as a safe git directory
git config --global --add safe.directory /workspace

# Turn off git info in the zsh prompt (avoids slowdowns)
git config oh-my-zsh.hide-info 1 || true

# Install Python dependencies
echo "Installing dependencies from requirements.txt..."
pip3 --disable-pip-version-check --no-cache-dir install --user --no-warn-script-location -r requirements.txt

# Start nginx
echo "Starting nginx..."
sudo nginx || true

# Wait for the DB container
echo "Waiting for the DB container to come online..."
/usr/local/bin/wait-for db:5432 -- echo "PostgreSQL ready"

# Run migrations
echo "Running migrations..."
./manage.py migrate --no-input || true

# Vendor the self-hosted SurveyJS bundles into the static tree
if [ -f vendor/package.json ]; then
    echo "Vendoring SurveyJS bundles..."
    (cd vendor && npm install && npm run sync) || true
fi

# Collect static files (after vendoring so the bundles are included)
echo "Collecting static files..."
./manage.py collectstatic --no-input || true

# Install client dependencies if the Nuxt client is present
if [ -f client/package.json ]; then
    echo "Installing client dependencies..."
    (cd client && npm install) || true
fi

sudo touch /.dev-ready

# Outside VS Code, launch a tmux session running the servers.
if [ -z "$EDITOR_VSCODE" ]; then
  echo "-----------------------------------------------------------------"
  echo "Ready!"
  echo "-----------------------------------------------------------------"
  echo "Launching tmux..."

  tmux start-server
  tmux new-session -d -s dev -c '/workspace'
  sleep 1
  tmux send-keys './manage.py runserver 8001' Enter
  if [ -f client/package.json ]; then
    tmux split-window -h -c '/workspace/client'
    tmux send-keys 'npm run dev' Enter
  fi
  tmux -2 attach-session -d -c '/workspace'

  echo "You've exited tmux. Send \"exit\" to stop the containers and quit."
  zsh
  exit 0
fi
