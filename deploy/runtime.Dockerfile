FROM node:22-bookworm AS node
FROM python:3.12-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest \
    && groupadd --gid 1000 runner \
    && useradd --uid 1000 --gid 1000 --create-home runner
ENV HOME=/home/runner PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
WORKDIR /workspace
CMD ["python3", "-c", "import time; time.sleep(2147483647)"]
