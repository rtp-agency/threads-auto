FROM python:3.12-slim

WORKDIR /srv/app

# шрифты для программной сборки скриншотов-уведомлений (кириллица + emoji).
# Roboto — ближе всего к SF Pro (iOS), даёт «1 в 1» вид скрина; DejaVu — фолбэк.
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-roboto fonts-dejavu-core fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
