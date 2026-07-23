#!/bin/bash
#
# Post-start script for the app container.
#

echo "Starting nginx..."
pidof nginx >/dev/null && echo "nginx is already running [ OK ]" || sudo nginx

echo "-----------------------------------------------------------------"
echo "Ready!"
echo "-----------------------------------------------------------------"
