FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY auth.py .
COPY logutil.py .
COPY vault_client.py .
COPY k8s_remediator.py .
COPY oidc.py .
COPY tracing.py .
COPY api_versioning.py .
COPY db_utils.py .
COPY rate_limit.py .

COPY main.py .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
