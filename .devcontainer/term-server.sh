#!/bin/zsh

if [ -n "$EDITOR_VSCODE" ]; then
  echo "Waiting for the initialization script to complete... Please wait..."
  until [ -f /.dev-ready ]
  do
      sleep 2
  done

  # DISABLE_AUTO_UPDATE: an interactive zsh runs Oh My Zsh's update check, and
  # its "Would you like to update? [Y/n]" prompt would block the server from
  # ever starting. Set here as well as in devcontainer.json so containers built
  # before that change are covered without a rebuild.
  DISABLE_AUTO_UPDATE=true zsh -i -c "./manage.py runserver 8001"
  clear
  echo "====== BACKEND API SERVER ======\n"
  echo "  Start the server using command:"
  echo "  ./manage.py runserver 8001\n"
  echo "================================\n"
fi
zsh
