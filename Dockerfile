FROM python:3.11-slim

WORKDIR /app

# generated from uv.lock via `uv export --frozen --no-dev --no-hashes --no-emit-project`; do not hand-edit
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "src.main"]
