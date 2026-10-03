FROM node:22-bookworm AS node
FROM ghcr.io/astral-sh/uv:0.12.22 AS uv
FROM python:3.12-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=uv /uv /usr/local/bin/uv
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global npm@10.9.2 pnpm@9.15.9 yarn@1.22.22 \
    && mkdir -p /opt/lsp \
    && npm install --prefix /opt/lsp --save-exact pyright@1.1.405 typescript@5.8.3 typescript-language-server@4.4.0 @vue/language-server@3.0.8 @vue/typescript-plugin@3.0.8 semver@7.7.2 vscode-langservers-extracted@4.10.0 \
    && uv pip install --target /opt/lsp/python ruff==0.12.12 packaging==25.0 \
    && mkdir -p /opt/lsp/bin \
    && ln -s /opt/lsp/python/bin/ruff /opt/lsp/bin/ruff \
    && groupadd --gid 1000 runner \
    && useradd --uid 1000 --gid 1000 --create-home runner
ENV HOME=/home/runner PYTHONDONTWRITEBYTECODE=1 UV_PYTHON_INSTALL_DIR=/home/runner/.local/share/uv/python UV_CACHE_DIR=/home/runner/.cache/uv
USER 1000:1000
WORKDIR /workspace
CMD ["python3", "-I", "-S", "-c", "import time; time.sleep(2147483647)"]
