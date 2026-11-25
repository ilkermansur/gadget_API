FROM python:3.12-slim

WORKDIR /opt/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Bütün durum /data volume'unda: SQLite, yüklenen dosyalar, NiceGUI oturum deposu.
ENV DATA_DIR=/data HOST=0.0.0.0 PORT=8080 PYTHONUNBUFFERED=1 \
    NICEGUI_STORAGE_PATH=/data/.nicegui
VOLUME /data
EXPOSE 8080
CMD ["python", "main.py"]
