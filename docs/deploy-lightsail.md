# Deploy to AWS Lightsail (single server)

Minimal W01 deployment: one Lightsail `micro_3_0` instance (1 GB, $7/month ≈ $0.01/hour) in
`eu-west-2`, running `docker-compose.yml` (API + Postgres). First done for AIENG-17 on 2026-10-08.

> **Cost rule:** a *stopped* Lightsail instance is still billed. Delete it when finished.

## Prerequisites

- `aws login --profile incidentops` (IAM user `aniket-admin`, never root)
- SSH key `~/.ssh/incidentops_lightsail` (already imported to Lightsail as `incidentops-key`)
- Docker running locally (OrbStack)

## 1. Create the server

```bash
cat > /tmp/userdata.sh <<'EOF'
#!/bin/bash
set -e
apt-get update -y
apt-get install -y docker.io docker-compose-v2
usermod -aG docker ubuntu
systemctl enable --now docker
touch /var/lib/cloud/docker-ready
EOF

aws lightsail create-instances --profile incidentops --region eu-west-2 \
  --instance-names incidentops-api-1 --availability-zone eu-west-2a \
  --blueprint-id ubuntu_24_04 --bundle-id micro_3_0 --key-pair-name incidentops-key \
  --user-data file:///tmp/userdata.sh --tags key=project,value=incidentops
```

## 2. Firewall: HTTP for everyone, SSH only for your IP

```bash
MYIP=$(curl -s https://checkip.amazonaws.com)
aws lightsail put-instance-public-ports --profile incidentops --region eu-west-2 \
  --instance-name incidentops-api-1 --port-infos \
  "[{\"fromPort\":80,\"toPort\":80,\"protocol\":\"tcp\",\"cidrs\":[\"0.0.0.0/0\"],\"ipv6Cidrs\":[\"::/0\"]},
    {\"fromPort\":22,\"toPort\":22,\"protocol\":\"tcp\",\"cidrs\":[\"$MYIP/32\"]}]"

IP=$(aws lightsail get-instance --profile incidentops --region eu-west-2 \
  --instance-name incidentops-api-1 --query instance.publicIpAddress --output text)
```

Postgres (5432) is never opened.

## 3. Build for the server's CPU (amd64) and ship it

```bash
docker buildx build --platform linux/amd64 -t incidentops-api:deploy --load .
docker save incidentops-api:deploy | gzip > /tmp/api-image.tar.gz

KEY=~/.ssh/incidentops_lightsail
# wait until cloud-init has installed Docker
until ssh -i $KEY -o StrictHostKeyChecking=accept-new ubuntu@$IP test -f /var/lib/cloud/docker-ready; do sleep 10; done

ssh -i $KEY ubuntu@$IP 'mkdir -p ~/incidentops'
scp -i $KEY /tmp/api-image.tar.gz docker-compose.yml ubuntu@$IP:~/incidentops/
```

## 4. Start (DB password generated on the server, never on your laptop or in git)

```bash
ssh -i $KEY ubuntu@$IP 'cd ~/incidentops \
  && gunzip -c api-image.tar.gz | docker load && docker tag incidentops-api:deploy incidentops-api:local \
  && rm api-image.tar.gz \
  && { [ -f .env ] || { umask 077; printf "POSTGRES_PASSWORD=%s\nAPI_PORT=80\n" "$(openssl rand -hex 24)" > .env; }; } \
  && docker compose up -d --no-build'
```

## 5. Verify

```bash
curl http://$IP/health            # {"status":"ok"}

# Full API check: runs every request and assertion in the Postman collection
npx newman run postman/IncidentOps-AI.postman_collection.json --env-var baseUrl=http://$IP
```

The IP changes on every new instance. A Lightsail static IP would keep it fixed, but an
**unattached** static IP costs $0.005/hour (about $3.60/month), and ours would be unattached
whenever the instance is deleted. Passing `baseUrl` as above avoids needing one.

## 6. Delete (always)

```bash
aws lightsail delete-instance --profile incidentops --region eu-west-2 --instance-name incidentops-api-1
ssh-keygen -R $IP
```

Check nothing billable is left: `get-instances`, `get-static-ips`, `get-disks`, `get-instance-snapshots`
should all return empty lists.

## Known limitations (W01)

- HTTP only, no TLS or domain.
- Postgres data lives on the instance disk and is lost on delete.
- Manual deploy over SSH; no CI/CD to AWS yet (GitHub OIDC later).
