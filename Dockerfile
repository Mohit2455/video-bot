FROM python:3.11

RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-dejavu-core \
    fonts-liberation \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]
