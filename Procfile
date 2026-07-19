web: gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 2 --timeout 30 --max-requests 1000 --max-requests-jitter 100
