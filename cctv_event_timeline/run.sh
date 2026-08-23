#!/usr/bin/with-contenv bashio
set -e
mkdir -p /data/timeline/cache/thumbs /data/timeline/cache/videos /data/timeline/tmp
chown -R timeline:timeline /data/timeline
exec su-exec timeline:timeline python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8099 --proxy-headers

