# GitODS 生产镜像：非 root 运行，锁定 python:3.12-slim-bookworm
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖层单独缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 非 root 运行用户；静态目录与数据目录授权
RUN groupadd --system gitods \
    && useradd --system --gid gitods --home /app gitods \
    && mkdir -p /data/gitods/staticfiles \
    && chown -R gitods:gitods /data/gitods /app

USER gitods

EXPOSE 8000

# collectstatic 幂等执行后启动 Gunicorn（2 workers / 120s 超时，
# 导入上限内实测后可调整；迁移是部署显式步骤，不在此处执行）
CMD ["sh", "-c", "python manage.py collectstatic --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 120 --access-logfile - --error-logfile -"]
