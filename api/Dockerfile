FROM python:3.12-slim
WORKDIR /app
COPY cli /app/cli
COPY api /app/api
RUN pip install --no-cache-dir ./cli ./api && useradd --create-home wms
USER wms
EXPOSE 8080
CMD ["sh", "-c", "python -m app.postgres && exec uvicorn app.main:app --host 0.0.0.0 --port 8080"]
