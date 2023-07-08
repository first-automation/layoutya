# Use the official lightweight Python image.
# https://hub.docker.com/_/python
# FROM python:3.11-slim
ARG PYTHON_ENV=python:3.11-slim
ARG POETRY_VERSION=1.6.1

# Allow statements and log messages to immediately appear in the logs
# ENV PYTHONUNBUFFERED True

FROM $PYTHON_ENV as build
# Allow statements and log messages to immediately appear in the logs
ENV PYTHONUNBUFFERED True

RUN apt-get update && \
    apt-get install --yes --no-install-recommends curl libcairo2-dev && \
    rm -rf /var/lib/apt/lists/*
RUN curl -sSL https://install.python-poetry.org | POETRY_VERSION=${POETRY_VERSION} python3 -

RUN mkdir -p /app
WORKDIR /app

COPY pyproject.toml poetry.lock ./

ENV PATH="/root/.local/bin:$PATH"
RUN poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-root

FROM $PYTHON_ENV as prod
# Allow statements and log messages to immediately appear in the logs
ENV PYTHONUNBUFFERED True
# Copy local code to the container image.
ENV APP_HOME /app
ENV PYTHONPATH "${PYTHONPATH}:${APP_HOME}"
WORKDIR $APP_HOME
COPY . ./

COPY --from=build /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=build /usr/lib/x86_64-linux-gnu/lib* /usr/lib/x86_64-linux-gnu/

CMD ["streamlit", "run", "layoutya/illust_synthe.py", "--server.port", "8080"]
