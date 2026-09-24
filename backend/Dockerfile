# 后端镜像：FastAPI + SQLite + ChromaDB
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# build-essential 用于少数需要编译的依赖（如 chroma-hnswlib）；curl 用于健康检查
# nodejs/npm 用于运行番茄小说 MCP server（npx -y @fysh925/mcp-server-fanqie）
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-embeddings.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# 可选：构建带本地中文向量模型的镜像（体积会大很多，约 +2GB）
# docker build --build-arg INSTALL_EMBEDDINGS=1 ...
ARG INSTALL_EMBEDDINGS=0
RUN if [ "$INSTALL_EMBEDDINGS" = "1" ]; then pip install -r requirements-embeddings.txt; fi

COPY app ./app

# 数据（SQLite / 向量库 / 上传文件）统一挂载到 /data
ENV DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
