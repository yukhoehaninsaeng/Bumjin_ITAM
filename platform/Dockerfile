# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
# Managed Cloud builds may mount a CA bundle without persisting it in image layers.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir -r requirements.txt; \
    else pip install --no-cache-dir -r requirements.txt; fi
COPY --chown=1000:1000 . ./
RUN ITAM_DEBUG=1 ITAM_DEV_SQLITE=1 python manage.py collectstatic --noinput \
    && mkdir -p /data/private-files && chown -R 1000:1000 /data
ENV ITAM_MEDIA_ROOT=/data/private-files
USER 1000:1000
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "60", "--access-logfile", "-"]
