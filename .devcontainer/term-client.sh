#!/bin/zsh

CLIENT_DIR=/workspace/client

# Must match devServer.port in client/nuxt.config.ts and the proxy_pass port in
# docker/configs/nginx-proxy.conf. 3001 rather than the Nuxt default of 3000, so
# the dev server does not collide with Red's Nuxt server.
CLIENT_PORT=3001

if [ -n "$EDITOR_VSCODE" ]; then
  echo "Waiting for the initialization script to complete... Please wait..."
  until [ -f /.dev-ready ]
  do
      sleep 2
  done

  if [ ! -f $CLIENT_DIR/package.json ]; then
    echo "====== CLIENT DEV SERVER ======\n"
    echo "  The Nuxt client is not present yet."
    echo "  It is added later in the project plan (the survey runner).\n"
    echo "================================\n"
    zsh
    exit 0
  fi

  # The task runs with cwd /workspace, which has no package.json.
  cd $CLIENT_DIR

  # Nuxt resolves its port with get-port-please, which falls back to the
  # 3000-3100 range when the configured port is taken - and 3000 is the port
  # we moved off to avoid colliding with Red. Refuse to start a second dev
  # server rather than let it drift onto 3000 while nginx keeps proxying to
  # the configured port.
  if ss -ltn 2>/dev/null | grep -qE ":${CLIENT_PORT}[[:space:]]"; then
    echo "====== CLIENT DEV SERVER ======\n"
    echo "  Port $CLIENT_PORT is already in use, so a dev server is already running."
    echo "  Find it with:  ss -ltnp | grep $CLIENT_PORT    (or: tmux ls)\n"
    echo "  Not starting a second one - Nuxt would silently fall back to"
    echo "  port 3000, colliding with Red while nginx proxies to $CLIENT_PORT.\n"
    echo "  Once the port is free:  npm run dev\n"
    echo "================================\n"
    zsh
    exit 0
  fi

  zsh -i -c "npm run dev"
  clear
  echo "====== CLIENT DEV SERVER ======\n"
  echo "  Start the client dev server using command:"
  echo "  npm run dev\n"
  echo "================================\n"
fi

zsh
