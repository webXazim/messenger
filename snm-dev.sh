#!/usr/bin/env bash
set -e

# cd ~/projects/messenger

case "$1" in
  start)
    docker compose up -d
    ;;

  backend)
    docker compose up -d --build web worker beat
    ;;

  frontend)
    docker compose build --no-cache frontend
    docker compose up -d frontend nginx
    ;;

  all)
    docker compose down
    docker compose up -d --build
    ;;

  nginx)
    docker compose restart nginx
    ;;

  ps)
    docker compose ps
    ;;

  logs-web)
    docker compose logs -f web
    ;;

  logs-nginx)
    docker compose logs -f nginx
    ;;

  stop)
    docker compose down
    ;;


  *)
    echo "Usage:"
    echo "  ./snm-dev.sh start"
    echo "  ./snm-dev.sh backend"
    echo "  ./snm-dev.sh frontend"
    echo "  ./snm-dev.sh all"
    echo "  ./snm-dev.sh nginx"
    echo "  ./snm-dev.sh ps"
    echo "  ./snm-dev.sh logs-web"
    echo "  ./snm-dev.sh logs-nginx"
    echo "  ./snm-dev.sh stop"
    exit 1
    ;;
esac