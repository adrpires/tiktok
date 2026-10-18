FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY downloader.py script_generator.py video_editor.py scheduler.py ./

ENV PYTHONUNBUFFERED=1 \
    TZ=America/Sao_Paulo \
    TIKTOK_STORAGE_STATE=/session/TIKTOK_STORAGE_STATE.json

ENTRYPOINT ["python", "scheduler.py"]
