# Deploy C: Oracle Cloud Always-Free VM

Always-on public instance of CloudGuardian AI at **zero cost** (all resources below
are inside the Oracle Always Free tier). This is the recommended "leave it running"
deployment. For short demo sessions, use Deployment B (Codespaces) instead.

**Constraints respected:** no payments, no credit card on file. Everything here
stays inside Always Free quota.

---

## 0. Why this shape

| Resource | Always-Free allowance | This deployment uses |
|---|---|---|
| Compute | Ampere A1.Flex: 4 OCPU + 24 GB RAM total | 2 OCPU / 12 GB (or E2.1.Micro 1/1 for a tiny demo) |
| VM storage | 200 GB boot volume total | ~15 GB |

`VM.Standard.E2.1.Micro` is free but only 1 OCPU/1 GB RAM — enough for the
compose stack alone; add the k3d fleet only with A1 (2 OCPU + 12 GB).

---

## 1. Create the instance (Console)

1. Compute → Instances → **Create instance**.
2. Image: **Ubuntu 24.04**, Shape: **VM.Standard.A1.Flex**, OCPU **2**, RAM **12 GB**.
3. **Add SSH key** (you must have one or generate with `ssh-keygen`).
4. Create. Note the **public IP**.
5. Open the security list for the VCN subnet — add these **Ingress** rules:

   | Source | Port | Use |
   |---|---|---|
   | 0.0.0.0/0 | TCP 22 | SSH |
   | 0.0.0.0/0 | TCP 80, 443 | Caddy/HTTPS |
   | 0.0.0.0/0 | TCP 3001 | Mission Control dashboard |
   | 0.0.0.0/0 | TCP 8010-8050 | backend APIs |
   | 0.0.0.0/0 | TCP 8001-8003 | simulated fleet |

---

## 2. Install the toolchain

```bash
ssh ubuntu@<PUBLIC_IP>

sudo apt-get update && sudo apt-get install -y git curl ca-certificates apt-transport-https

# Docker engine + compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu && newgrp docker

# kubectl
curl -sL "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
  -o /tmp/kubectl && sudo mv /tmp/kubectl /usr/local/bin/kubectl && chmod +x /usr/local/bin/kubectl

# k3d
curl -s -o /tmp/k3d-install.sh https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh
bash /tmp/k3d-install.sh
```

---

## 3. Clone + secrets

```bash
git clone git@github.com:Apollonartemius/CLOUD-GUARDIAN.git
cd CLOUD-GUARDIAN
mkdir -p .kube

# Required secrets (never commit these). Genuinely random, per-instance.
cat > .env <<'EOF'
JWT_SECRET=$(openssl rand -hex 32)
REFRESH_SECRET=$(openssl rand -hex 32)
ALERT_HOOK_SECRET=$(openssl rand -hex 20)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
EOF
chmod 600 .env

# OPTIONAL: enable the AI copilot's LLM answers (e.g. Anthropic/OpenAI key).
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
```

> If the host rebooted, `/tmp` is empty: run `./start.sh` once — it restores the
> vault secrets file automatically (see the FUSE note in `.env.example`).

---

## 4. Point the dashboard at this host (build-time)

The dashboard now reads `<VITE_BACKEND_HOST>` at build time (default: the
browser's own host). For a plain-IP deployment:

```bash
# http + IP (no HTTPS):
VITE_BACKEND_HOST=http://$(curl -4 -s ifconfig.me) docker compose build dashboard

# or with a domain + Caddy HTTPS below: VITE_BACKEND_HOST=https://cg.example.com
docker compose up -d --build dashboard
```

---

## 5. Boot the fleet + platform

```bash
# (a) build + import the simulated fleet image
docker build -t cloudguardian-ai-simulated-service:latest services/simulated-service
docker network inspect cloudguardian-net >/dev/null 2>&1 || docker network create cloudguardian-net

# (b) create the k3d fleet cluster
k3d cluster create cloudguardian \
  --port "8001-8003:30001-30003@server:0" \
  -v /etc/ssl/certs:/etc/ssl/certs

# (c) import the image + deploy the fleet manifests
k3d image import cloudguardian-ai-simulated-service:latest -c cloudguardian
k3d kubeconfig get cloudguardian > .kube/config
kubectl apply -f k8s/ --kubeconfig .kube/config
kubectl rollout status deployment --all --timeout=180s

# (d) bring up the full monitoring stack
./start.sh
```

**Smoke test**

```bash
curl -sf http://localhost:3001/api/health && echo "dashboard ok"
curl -sf http://localhost:8030/health && echo "decision-engine ok"
curl -sf http://localhost:9090/-/ready && echo "prometheus ok"
curl -sf http://localhost:8001/health && echo "fleet auth-service ok"
```

---

## 6. HTTPS with a domain (recommended)

Add an `A` record for `cg.example.com` → `<PUBLIC_IP>`, then:

```yaml
# docker-compose.override.yml (gitignored is fine, or commit it - it's optional)
services:
  caddy:
    image: caddy:2
    container_name: caddy
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
    depends_on: [dashboard, decision-engine]
```

```yaml
# Caddyfile
cg.example.com {
    encode zstd gzip
    reverse_proxy dashboard:80
}
cg-backend.example.com {
    encode zstd gzip
    reverse_proxy decision-engine:8000
}
```

Rebuild the dashboard with `VITE_BACKEND_HOST=https://cg-backend.example.com`.
Caddy auto-provisions a Let's Encrypt certificate (ports 80/443 open, no card).

---

## 7. Keeping it alive

```bash
# survive reboots: restart the platform + re-apply fleet after boot
(crontab -l 2>/dev/null; echo "@reboot cd $HOME/CLOUD-GUARDIAN && ./start.sh && kubectl apply -f k8s/ --kubeconfig .kube/config") | crontab -
```

> Note: the security group must keep 8001-8003 exposed only if you want the
> fleet reachable from a browser (risk), otherwise tune `NO_PUBLIC` in compose.

---

## 8. Cost profile

Always Free: compute + boot volume + public IP + Let's Encrypt certs = **$0/mo**.
Egress inside the 10 TB/month free allowance is virtually unused by this demo.