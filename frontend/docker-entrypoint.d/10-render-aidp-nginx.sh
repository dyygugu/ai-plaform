#!/bin/sh
set -eu

api_prefix="${AIDP_API_PREFIX:-/api/v1}"
case "$api_prefix" in
  /*) ;;
  *) api_prefix="/$api_prefix" ;;
esac
api_prefix="$(printf '%s' "$api_prefix" | sed 's#//*#/#g; s#/*$##')"
if [ -z "$api_prefix" ]; then
  api_prefix="/api/v1"
fi
case "$api_prefix" in
  *[!A-Za-z0-9_./-]*)
    echo "Invalid AIDP_API_PREFIX: only letters, numbers, underscore, dot, slash and dash are allowed." >&2
    exit 1
    ;;
esac

write_proxy_locations() {
  path="$1"
  cat <<EOF
  location = $path {
    proxy_pass http://api:8787\$request_uri;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-AIDP-Client-IP \$remote_addr;
    proxy_set_header X-Forwarded-For "";
    proxy_set_header CF-Connecting-IP "";
    proxy_set_header X-Forwarded-Proto \$scheme;
  }

  location ^~ $path/ {
    proxy_pass http://api:8787\$request_uri;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-AIDP-Client-IP \$remote_addr;
    proxy_set_header X-Forwarded-For "";
    proxy_set_header CF-Connecting-IP "";
    proxy_set_header X-Forwarded-Proto \$scheme;
  }

EOF
}

{
  cat <<'EOF'
server {
  listen 80;
  server_name _;

  root /usr/share/nginx/html;
  index index.html;

  location = /aidp-runtime-config.js {
    proxy_pass http://api:8787/aidp-runtime-config.js;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-AIDP-Client-IP $remote_addr;
    proxy_set_header X-Forwarded-For "";
    proxy_set_header CF-Connecting-IP "";
    proxy_set_header X-Forwarded-Proto $scheme;
  }

EOF

  write_proxy_locations "/api"
  if [ "$api_prefix" != "/api" ]; then
    write_proxy_locations "$api_prefix"
  fi

  cat <<'EOF'
  location / {
    try_files $uri $uri/ /index.html;
  }
}
EOF
} > /etc/nginx/conf.d/default.conf
