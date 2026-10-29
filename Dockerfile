FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY app.py .
COPY templates ./templates
COPY static ./static

RUN mkdir -p /data

EXPOSE 5001

# 1 seul worker : le verrou SQLite de l'app est local au processus
CMD ["gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:5001", "app:app"]