# Package a wheel built on matching Ubuntu 24.04 / Linux / CPython 3.12.
FROM ubuntu:25.10
RUN apt-get update && apt-get install -y --no-install-recommends python3.12-venv libsqlite3-0 libstdc++6 bubblewrap util-linux ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 sturgis \
    && python3.12 -m venv /opt/sturgis
ARG STURGIS_WHEEL
COPY ${STURGIS_WHEEL} /tmp/wheels/
RUN /opt/sturgis/bin/python -m pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels
USER sturgis
ENV STURGIS_ROOT=/home/sturgis/data STURGIS_MODE=self_hosted STURGIS_HOST=0.0.0.0 STURGIS_PORT=8787
VOLUME ["/home/sturgis/data"]
EXPOSE 8787
ENTRYPOINT ["/opt/sturgis/bin/sturgis", "serve"]
# Set STURGIS_PUBLIC_ORIGIN to the operator's actual external HTTPS origin.
