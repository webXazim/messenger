web: daphne -b 0.0.0.0 -p ${PORT:-8000} config.asgi:application
worker: celery -A config worker -l info
beat: celery -A config beat -l info
