FROM python:3.13-slim

WORKDIR /app

# Copy requirements file and install dependencies
COPY backend/ /app/backend

RUN pip install --no-cache-dir -U -r backend/requirements.txt

ENV PYTHONPATH=/app
EXPOSE 8080

ENTRYPOINT ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "4", "--no-access-log"]