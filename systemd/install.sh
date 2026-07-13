#!/bin/bash
# Install and start the webcam pipeline systemd services.
# Run as: sudo bash /home/user/webcam/systemd/install.sh
set -e

# Clean leftovers of the official Ollama installer: 2GB of libs on the
# root disk and a unit pointing at a missing binary/nonexistent user.
# Our Ollama lives at /mnt/models/ollama-bin and runs as user.
systemctl stop ollama 2>/dev/null || true
rm -rf /usr/local/lib/ollama /usr/local/bin/ollama /usr/share/ollama
rm -rf /etc/systemd/system/ollama.service.d

cp /home/user/webcam/systemd/webcam-pipeline@.service /etc/systemd/system/
cp /home/user/webcam/systemd/webcam-api.service /etc/systemd/system/
cp /home/user/webcam/systemd/ollama.service /etc/systemd/system/
systemctl daemon-reload

# Stop any ad-hoc watchers so systemd owns the processes
pkill -f "create-inde[x].sh" || true
pkill -x inotifywait || true
sleep 1

# Stop any ad-hoc API/ollama instances so systemd owns them
pkill -f "api_serve[r].py" || true
pkill -f "ollama-bin/bin/ollam[a]" || true

systemctl enable --now webcam-pipeline@Webcam21 webcam-pipeline@Webcam22 webcam-api ollama

# The @reboot create-index cron entries are superseded by systemd. Keep a
# separate cron watchdog so a "active" unit that is stuck mid-sweep still
# gets restarted and retention still runs from settings.json.
crontab -u user -l 2>/dev/null | grep -v create-index.sh | grep -v webcam/tools/watchdog | crontab -u user - || true
install -m 644 /home/user/webcam/systemd/webcam-watchdog.cron /etc/cron.d/webcam-watchdog
chmod +x /home/user/webcam/tools/watchdog.sh

systemctl --no-pager status webcam-pipeline@Webcam21 webcam-pipeline@Webcam22 | head -20
echo "Done. Logs: journalctl -u webcam-pipeline@Webcam21 -f"
echo "Watchdog: /etc/cron.d/webcam-watchdog  (log: /home/user/webcam/watchdog.log)"
