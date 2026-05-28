# Understanding Kubernetes Headless Services: PostgreSQL Replication Demo

## Introduction
This project demonstrates Kubernetes Headless Services using a real-world PostgreSQL primary-replica setup. It shows how Headless and ClusterIP services work together for database write/read separation.

## What is a Headless Service?
A regular Kubernetes Service gives you one IP that load balances across pods. A **Headless Service** is different — it has no ClusterIP. Instead, DNS returns the IP of every pod behind the service.

**Why is this useful?** When you need to talk to a *specific* pod — like a database primary — you need its direct address, not a load balancer.

Key points:
- `clusterIP: None` makes it headless
- DNS returns all pod IPs (not one service IP)
- Each pod gets its own DNS: `<pod-name>.<service-name>.<namespace>.svc.cluster.local`

## Architecture

```
                 ┌────────────────────────────────────────────┐
                 │           Kubernetes Cluster               │
                 │                                            │
  Write App      │   Headless Service (postgres)              │
  ──────────────►│   clusterIP: None                         │
  DB_HOST:       │     ├─► postgres-0.postgres (Primary)     │
  postgres-0.    │     └─► postgres-1.postgres (Replica)     │
  postgres       │                    ▲                       │
                 │                    │ streaming replication  │
                 │                    │                       │
  Read App       │   ClusterIP Service (postgres-read)       │
  ──────────────►│   10.x.x.x (load balanced)               │
  DB_HOST:       │     ├─► postgres-0                        │
  postgres-read  │     └─► postgres-1                        │
                 └────────────────────────────────────────────┘
```

### How Both Services Work Together

| Service | Type | Purpose | DNS Returns |
|---------|------|---------|-------------|
| `postgres` | Headless (`clusterIP: None`) | Direct pod access for writes | Individual pod IPs |
| `postgres-read` | ClusterIP | Load-balanced reads | Single service IP |

- **Write App** → connects to `postgres-0.postgres` (headless) → always hits the primary
- **Read App** → connects to `postgres-read` (ClusterIP) → load balanced across both pods

## Components

### PostgreSQL StatefulSet (2 replicas)
- `postgres-0`: Primary — handles all writes
- `postgres-1`: Replica — streams data from primary, serves reads
- Replication: PostgreSQL built-in streaming replication

### Write Application (Port 30000)
- Connects to `postgres-0.postgres` via headless service
- Registers users (INSERT operations)

### Read Application (Port 30001)
- Connects to `postgres-read` ClusterIP service
- Reads users (SELECT operations, load balanced)

## Directory Structure
```
├── kubernetes/
│   ├── postgres-statefulset.yaml   # StatefulSet + both Services
│   ├── write-app.yaml              # Write app Deployment + NodePort
│   └── read-app.yaml               # Read app Deployment + NodePort
├── write-app/
│   ├── app.py
│   ├── index.html
│   ├── requirements.txt
│   └── Dockerfile
└── read-app/
    ├── app.py
    ├── index.html
    ├── requirements.txt
    └── Dockerfile
```

## Quick Start

### Prerequisites
- Kubernetes cluster
- kubectl configured
- A default StorageClass (see below)
- Docker (for building images)

### Step 1: Set up Storage (if needed)

```bash
# Install OpenEBS for local storage
helm repo add openebs https://openebs.github.io/charts
helm repo update
helm install openebs --namespace openebs openebs/openebs \
  --set engines.replicated.mayastor.enabled=false \
  --create-namespace

# Set as default storage class
kubectl patch storageclass openebs-hostpath -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
```

### Step 2: Build and Load Images

```bash
# Build images
docker build -t headless-write:1 ./write-app/
docker build -t headless-read:1 ./read-app/

# Load images onto cluster nodes (for containerd clusters)
docker save headless-write:1 -o /tmp/headless-write.tar
docker save headless-read:1 -o /tmp/headless-read.tar

for node in <your-nodes>; do
  scp /tmp/headless-write.tar /tmp/headless-read.tar $node:/tmp/
  ssh $node "sudo ctr -n k8s.io images import /tmp/headless-write.tar && \
             sudo ctr -n k8s.io images import /tmp/headless-read.tar"
done
```

### Step 3: Deploy

```bash
# Deploy PostgreSQL (StatefulSet + Services)
kubectl apply -f kubernetes/postgres-statefulset.yaml

# Wait for both postgres pods to be ready
kubectl get pods -w

# Deploy applications
kubectl apply -f kubernetes/write-app.yaml
kubectl apply -f kubernetes/read-app.yaml
```

### Step 4: Verify

```bash
# Check all pods are running
kubectl get pods

# Verify replication is working
kubectl exec postgres-0 -- psql -U postgres -c "SELECT client_addr, state FROM pg_stat_replication;"

# Verify replica is in read-only mode
kubectl exec postgres-1 -- psql -U postgres -c "SELECT pg_is_in_recovery();"
```

## Testing the Demo

### Test Write (via Headless Service)
```bash
curl -X POST http://<node-ip>:30000/submit \
  -H "Content-Type: application/json" \
  -d '{"name":"John Doe","email":"john@example.com"}'
```

### Test Read (via ClusterIP Service)
```bash
curl http://<node-ip>:30001/users
```

### Observe DNS Differences
```bash
# Headless service — returns individual pod IPs
kubectl run -it --rm debug --image=busybox --restart=Never -- nslookup postgres

# ClusterIP service — returns single service IP
kubectl run -it --rm debug --image=busybox --restart=Never -- nslookup postgres-read
```

## Key Learning Points

1. **Headless Service** (`clusterIP: None`): Returns individual pod IPs via DNS. Use when you need to reach a specific pod.

2. **ClusterIP Service** (default): Returns one service IP that load balances. Use for normal traffic distribution.

3. **Together**: Headless for writes to the primary, ClusterIP for load-balanced reads — a common real-world database pattern.

## Production Notes
This is a demo. For production:
- Use Kubernetes Secrets for credentials
- Configure proper storage classes
- Use a PostgreSQL operator (like CloudNativePG) for production replication
- Implement backup strategies