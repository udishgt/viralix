FROM python:3.11-slim
WORKDIR /app

# system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# copy and install python deps from the repo-root build context
COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip setuptools \
    && pip install --no-cache-dir -r ./requirements.txt

# copy backend code into the image root so uvicorn can import server:app
COPY backend/*.py ./

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
