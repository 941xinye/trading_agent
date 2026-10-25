# 链析 ChainLens · 币圈行情分析师 Agent
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 安装系统依赖（tzdata 用于时区，curl 用于健康检查）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖，利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝源码
COPY crypto_analyst/ ./crypto_analyst/
COPY main.py .

# 创建运行时目录
RUN mkdir -p .cache .state reports

# 默认运行内置调度（异动监控 + 每日定时简报）
# 可通过 docker-compose 或 command 覆盖为 api / feishu-hook 等
CMD ["python", "main.py", "serve"]
