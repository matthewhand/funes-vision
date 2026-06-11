#!/bin/bash
# Install and start the webcam pipeline systemd services.
# Run as: sudo bash /home/user/webcam/systemd/install.sh
set -e

cp /home/user/webcam/systemd/webcam-pipeline@.service /etc/systemd/system/
cp /home/user/webcam/systemd/webcam-api.service /etc/systemd/system/
systemctl daemon-reload

# Stop any ad-hoc watchers so systemd owns the processes
pkill -f "create-inde[x].sh" || true
pkill -x inotifywait || true
sleep 1

# Stop any ad-hoc API instance so systemd owns it
pkill -f "api_serve[r].py" || true

systemctl enable --now webcam-pipeline@Webcam21 webcam-pipeline@Webcam22 webcam-api

# The @reboot cron entries are superseded by systemd
crontab -u user -l 2>/dev/null | grep -v create-index.sh | crontab -u user - || true

systemctl --no-pager status webcam-pipeline@Webcam21 webcam-pipeline@Webcam22 | head -20
echo "Done. Logs: journalctl -u webcam-pipeline@Webcam21 -f"
