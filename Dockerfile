# Playwright's image ships the system libraries Chromium needs, plus Python 3.12 (Ubuntu noble).
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir uv==0.5.11

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --extra dev

COPY src ./src
COPY sites ./sites
COPY tasks ./tasks
COPY tests ./tests
RUN uv sync --frozen --extra dev \
    # The locked Playwright package may be newer than the image's browsers: fetch the matching build.
    && playwright install chromium

# Run as the unprivileged user the base image provides.
RUN chown -R pwuser /app
USER pwuser

ENTRYPOINT ["wta"]
CMD ["run", "tasks/", "--policy", "scripted", "--report", "out"]
