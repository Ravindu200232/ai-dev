#!/usr/bin/env bash
# Deployment Agent release script. Runs on the EC2 instance via SSM.
# Usage: BUCKET=... SHA=... SECRET_ID=... release.sh
set -euo pipefail

: "${BUCKET:?BUCKET is required}"
: "${SHA:?SHA is required}"
: "${SECRET_ID:?SECRET_ID is required}"

APP_DIR=/opt/app
RELEASES="$APP_DIR/releases"
SHARED="$APP_DIR/shared"
RELEASE="$RELEASES/$SHA"
PORT=<%.Port%>
HEALTH_PATH=<%.HealthPath%>

mkdir -p "$RELEASES" "$SHARED"

echo "==> Downloading release $SHA"
aws s3 cp "s3://$BUCKET/releases/$SHA/app.tar.gz" /tmp/"$SHA".tar.gz

echo "==> Unpacking"
rm -rf "$RELEASE"
mkdir -p "$RELEASE"
tar -xzf /tmp/"$SHA".tar.gz -C "$RELEASE"
rm -f /tmp/"$SHA".tar.gz

echo "==> Refreshing runtime environment"
env_tmp=$(mktemp)
chmod 0600 "$env_tmp"
{
  echo "NODE_ENV=production"
  echo "PORT=$PORT"
  echo "HOSTNAME=127.0.0.1"
  aws secretsmanager get-secret-value --secret-id "$SECRET_ID" \
    --query SecretString --output text \
    | jq -r --argjson allowed '<%.SecretNames%>' \
        'to_entries[] | select(.key as $k | $allowed | index($k)) | "\(.key)=\(.value|@sh)"'
} > "$env_tmp"
install -o root -g nextjs -m 0640 "$env_tmp" "$SHARED/.env"
rm -f "$env_tmp"

chown -R nextjs:nextjs "$RELEASE"
ln -sfn "$RELEASE" "$APP_DIR/current.new"
mv -Tf "$APP_DIR/current.new" "$APP_DIR/current"

echo "==> Restarting service"
systemctl restart nextjs

echo "==> Waiting for health check"
for attempt in $(seq 1 30); do
  if curl -fsS --max-time 5 "http://127.0.0.1:$PORT$HEALTH_PATH" >/dev/null 2>&1; then
    echo "Health check passed on attempt $attempt"
    # Keep the five most recent releases so rollback has somewhere to go.
    ls -1dt "$RELEASES"/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf
    echo "$SHA" > "$SHARED/current-sha"
    exit 0
  fi
  sleep 2
done

echo "Health check failed; dumping recent service logs" >&2
journalctl -u nextjs -n 50 --no-pager >&2 || true
exit 1
