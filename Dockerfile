FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    TZ=Asia/Tokyo

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential tzdata fonts-noto fonts-noto-cjk fonts-noto-color-emoji fonts-noto-extra \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -U pip && pip install -r requirements.txt

COPY src ./src
COPY app ./app

RUN mkdir -p /app/config /app/db

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser


ENTRYPOINT ["python"]
CMD ["app/tele_osint_cli.py"] 