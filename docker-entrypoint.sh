#!/bin/sh
# One entrypoint for both services: the web app and the reminder loop.
set -e

case "$1" in
  serve)
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    exec python serve.py --host 0.0.0.0 --port 8000 --proxy
    ;;
  reminders)
    # No cron in the image: the loop is visible in `docker compose logs`.
    # Both commands are idempotent, so an hourly loop is safe; attendance_alerts
    # decides from the Tashkent hour whether to send the late/absentee messages.
    while true; do
      python manage.py send_reminders || echo "send_reminders failed; retrying next hour"
      python manage.py attendance_alerts || echo "attendance_alerts failed; retrying next hour"
      sleep "${REMINDER_INTERVAL:-3600}"
    done
    ;;
  *)
    exec "$@"
    ;;
esac
