#!/usr/bin/env bash

set -e

USERNAME=${1:-"dev"}
USER_UID=${2:-"1000"}
USER_GID=${3:-"1000"}

export DEBIAN_FRONTEND=noninteractive

# Install the packages the dev container needs
apt-get update
apt-get -y install --no-install-recommends nginx tmux sudo 2>&1
apt-get autoremove -y

# Create or update a non-root user to match the host UID/GID.
group_name="${USERNAME}"
if id -u "${USERNAME}" > /dev/null 2>&1; then
  if [ "$USER_GID" != "$(id -g "$USERNAME")" ]; then
    group_name="$(id -gn "$USERNAME")"
    groupmod --gid "$USER_GID" "${group_name}"
    usermod --gid "$USER_GID" "$USERNAME"
  fi
  if [ "$USER_UID" != "$(id -u "$USERNAME")" ]; then
    usermod --uid "$USER_UID" "$USERNAME"
  fi
else
  groupadd --force --gid "$USER_GID" "$USERNAME"
  useradd -s /bin/bash --uid "$USER_UID" --gid "$USERNAME" -m "$USERNAME" || true
fi

# Grant passwordless sudo to the non-root user
echo "$USERNAME ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/"$USERNAME"
chmod 0440 /etc/sudoers.d/"$USERNAME"

# Restore .bashrc / .profile from the skeleton if missing or empty
if [ ! -s "/home/${USERNAME}/.bashrc" ]; then
  cp /etc/skel/.bashrc "/home/${USERNAME}/.bashrc"
fi
if [ ! -s "/home/${USERNAME}/.profile" ]; then
  cp /etc/skel/.profile "/home/${USERNAME}/.profile"
fi

# Install Oh My Zsh for the non-root user
oh_my_install_dir="/home/${USERNAME}/.oh-my-zsh"
if [ ! -d "${oh_my_install_dir}" ]; then
  user_rc_file="/home/${USERNAME}/.zshrc"
  umask g-w,o-w
  mkdir -p "${oh_my_install_dir}"
  git clone --depth=1 \
    -c core.eol=lf \
    -c core.autocrlf=false \
    "https://github.com/ohmyzsh/ohmyzsh" "${oh_my_install_dir}" 2>&1
  template_path="${oh_my_install_dir}/templates/zshrc.zsh-template"
  # Turn the update check off *before* the template's `source $ZSH/oh-my-zsh.sh`
  # - that is where the check runs, so settings appended below it are too late.
  # An interactive shell that is a tty (the `zsh -i -c` in term-server.sh) would
  # otherwise block on "[oh-my-zsh] Would you like to update? [Y/n]" and never
  # start the server. zstyle is the current setting; the variable is the legacy
  # fallback for older Oh My Zsh checkouts.
  {
    echo "zstyle ':omz:update' mode disabled"
    echo "DISABLE_AUTO_UPDATE=true"
    cat "${template_path}"
  } > "${user_rc_file}"
  cd "${oh_my_install_dir}"
  git repack -a -d -f --depth=1 --window=1
fi
chown -R "${USERNAME}:${group_name}" "/home/${USERNAME}"
