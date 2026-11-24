FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Непривилегированный пользователь
RUN addgroup -S bot \
 && adduser -S -D -h /app -s /sbin/nologin -G bot bot

COPY requirements.txt ./
# --no-compile: не тащим .pyc зависимостей; pip/setuptools в рантайме не нужны
RUN pip install --no-cache-dir --no-compile -r requirements.txt \
 && (python -m pip uninstall -y pip setuptools wheel || true) \
 && rm -rf /root/.cache \
    /usr/local/lib/python3.12/ensurepip \
    /usr/local/lib/python3.12/idlelib \
    /usr/local/lib/python3.12/lib2to3 \
    /usr/local/lib/python3.12/tkinter \
    /usr/local/lib/python3.12/turtledemo

COPY app ./app

RUN mkdir -p /data && chown -R bot:bot /app /data

USER bot

CMD ["python", "-m", "app"]
