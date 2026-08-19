FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY bridge ./bridge
RUN pip install --no-cache-dir .

ENV DATABASE_PATH=/data/smartops_teams_support.db
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "bridge.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
