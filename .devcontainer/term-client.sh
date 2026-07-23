#!/bin/zsh

if [ -n "$EDITOR_VSCODE" ]; then
  echo "Waiting for the initialization script to complete... Please wait..."
  until [ -f /.dev-ready ]
  do
      sleep 2
  done

  if [ ! -f /workspace/client/package.json ]; then
    echo "====== CLIENT DEV SERVER ======\n"
    echo "  The Nuxt client is not present yet."
    echo "  It is added later in the project plan (the survey runner).\n"
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
