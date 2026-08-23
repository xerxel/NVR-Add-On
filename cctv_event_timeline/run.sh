#!/usr/bin/with-contenv bashio
set -e
mkdir -p /data/timeline/cache/thumbs /data/timeline/cache/videos /data/timeline/tmp
chown -R timeline:timeline /data/timeline

# Home Assistant owns /data/options.json and may create it with mode 0600. Keep
# that Supervisor-managed file unchanged, while giving the unprivileged service
# a runtime-only copy with equally restrictive permissions.
runtime_options="/run/cctv-event-timeline-options.json"
umask 077
if [[ -f /data/options.json ]]; then
    cp /data/options.json "${runtime_options}"
else
    printf '{}\n' > "${runtime_options}"
fi
chown timeline:timeline "${runtime_options}"
chmod 0600 "${runtime_options}"

exec su-exec timeline:timeline env OPTIONS_FILE="${runtime_options}" \
    python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8099 --proxy-headers
