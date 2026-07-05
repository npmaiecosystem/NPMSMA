FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    netcat-openbsd libsm6 libxext6 libxrender-dev ffmpeg poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt \
    && pip install --no-cache-dir --break-system-packages uvicorn moviepy==1.0.3

COPY . .

EXPOSE 7860

EXPOSE 7860

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "4"]
