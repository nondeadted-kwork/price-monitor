FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Europe/Moscow
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY monitor ./monitor
COPY config.yaml .
RUN useradd --create-home app && mkdir -p /app/output && chown app /app/output
USER app
CMD ["python", "-m", "monitor", "run", "--every", "60m"]
