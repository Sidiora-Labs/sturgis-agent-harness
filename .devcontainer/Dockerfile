FROM node:26.12.0-bookworm-slim AS node
FROM python:3.12-slim-bookworm

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update \
    && apt-get install --yes --no-install-recommends \
       bash build-essential ca-certificates cmake curl git libsqlite3-dev \
       bubblewrap util-linux shellcheck \
    && rm -rf /var/lib/apt/lists/*

# Install the Linux libraries for the exact Playwright version in the lockfile.
# Browser binaries and project dependencies are installed later as the developer.
COPY package.json package-lock.json /tmp/sturgis-browser-deps/
RUN cd /tmp/sturgis-browser-deps \
    && npm ci --ignore-scripts \
    && npx --no-install playwright install-deps chromium \
    && rm -rf /tmp/sturgis-browser-deps /root/.npm /var/lib/apt/lists/*

RUN groupadd --gid 1000 sturgis \
    && useradd --uid 1000 --gid sturgis --create-home --shell /bin/bash sturgis \
    && install -d -o sturgis -g sturgis /workspaces/sturgis
USER sturgis
WORKDIR /workspaces/sturgis
ENV STURGIS_MODE=self_hosted \
    STURGIS_HOST=127.0.0.1 \
    STURGIS_PORT=8787 \
    STURGIS_PUBLIC_ORIGIN=http://127.0.0.1:8787 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
CMD ["sleep", "infinity"]
