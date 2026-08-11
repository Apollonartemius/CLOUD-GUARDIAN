#!/bin/bash
# Runs once when the VM first boots. Installs Python, writes out the
# EXACT same main.py used by the local Docker-based services (so this
# is genuinely the same application, just running on a real cloud VM
# instead of a container on your laptop), and runs it as a systemd
# service so it survives reboots.
set -e

apt-get update -y
apt-get install -y python3-pip
pip3 install --break-system-packages fastapi "uvicorn[standard]" prometheus-client

mkdir -p /opt/cloudguardian

cat > /opt/cloudguardian/main.py <<'PYEOF'
${main_py_content}
PYEOF

cat > /etc/systemd/system/cloudguardian.service <<EOF
[Unit]
Description=CloudGuardian simulated service (real GCP VM)
After=network.target

[Service]
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
WorkingDirectory=/opt/cloudguardian
Environment=SERVICE_NAME=${service_name}
Environment=BASE_CPU=${base_cpu}
Environment=BASE_MEM=${base_mem}
Environment=BASE_LATENCY_MS=${base_latency}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cloudguardian
systemctl start cloudguardian
