FROM python:3.12-slim

# Small, reproducible image
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first for better layer caching
COPY requirements.txt .
RUN pip install -r requirements.txt

# App code
COPY . .

# Railway sets $PORT at runtime; default to 5000 for local `docker run`
ENV PORT=5000
EXPOSE 5000

# Shell form so ${PORT} is expanded at runtime
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT}
