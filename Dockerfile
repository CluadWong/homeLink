# 两段构建：Node 只用于产出 frontend/dist，运行镜像不装 Node。
# 构建：docker compose build
# 说明见 README「Linux / Docker 部署」。

FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOMELINK_CONFIG_DIR=/app/config \
    HOMELINK_DB_PATH=/app/data/homelink.db
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[agent]"
COPY config ./config
COPY --from=frontend /build/dist ./frontend/dist
RUN mkdir -p data logs backups

EXPOSE 8000 8100
# 默认只起后端；daemon 由 compose 的 agent 服务覆盖 command。
CMD ["uvicorn", "homelink.interface.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
