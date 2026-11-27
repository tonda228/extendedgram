FROM python:3.14-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

COPY requirements.txt .

RUN uv pip install --system --no-cache -r requirements.txt

COPY . ./host/

CMD ["python", "-m", "host.main"]
