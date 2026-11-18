FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "fastapi==0.115.*" "uvicorn[standard]==0.32.*" "httpx==0.27.*"

COPY app.py /app/app.py

ENV UPSTREAM_BASE=http://llamacpp-embed:11436 \
    TARGET_DIM=2000

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
