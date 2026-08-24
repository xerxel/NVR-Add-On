ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG BUILD_VERSION

LABEL \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="app" \
    io.hass.version="${BUILD_VERSION}"

RUN apk add --no-cache ffmpeg su-exec tzdata \
    && addgroup -S timeline \
    && adduser -S -D -H -G timeline timeline

WORKDIR /app
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt
COPY app /app/app
COPY run.sh /run.sh
RUN chmod 0755 /run.sh

EXPOSE 8099
CMD ["/run.sh"]
