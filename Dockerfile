FROM python:3.12.3-slim
LABEL authors="EricUgoPaque"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements/ ./requirements/
RUN pip install --upgrade pip \
    && pip install -r requirements/all.txt
COPY . .
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]