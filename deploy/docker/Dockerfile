FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/orchestrator

WORKDIR /app

COPY orchestrator/requirements.txt /app/orchestrator/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/orchestrator/requirements.txt

COPY orchestrator /app/orchestrator

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "/app/orchestrator", "--host", "0.0.0.0", "--port", "8080"]
