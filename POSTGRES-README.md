# PostgreSQL StatefulSet — Explained Line by Line

This document walks through every section of [`postgres-statefulset.yaml`](file:///home/laborant/kubernetes-headless-project/kubernetes/postgres-statefulset.yaml), explaining **what** each part does and **why** it's needed.

---

## Table of Contents
1. [The Big Picture](#the-big-picture)
2. [ConfigMap — The Init Script](#1-configmap--the-init-script)
3. [Headless Service](#2-headless-service)
4. [ClusterIP Service](#3-clusterip-service)
5. [StatefulSet](#4-the-statefulset)
   - [Init Container — Role Detection & Cloning](#init-container--role-detection--cloning)
   - [Main Container — PostgreSQL Server](#main-container--postgresql-server)
   - [Volumes](#volumes)
6. [How Replication Works End-to-End](#how-replication-works-end-to-end)
7. [PostgreSQL Commands Reference](#postgresql-commands-reference)

---

## The Big Picture

```
┌─────────────────────────────────────────────────────────┐
│                   StatefulSet (2 replicas)               │
│                                                         │
│   postgres-0 (Primary)          postgres-1 (Replica)    │
│   ┌─────────────────┐          ┌─────────────────┐     │
│   │  Accepts writes │──WAL──►  │  Read-only copy │     │
│   │  & reads        │ stream   │  of primary     │     │
│   └────────┬────────┘          └────────┬────────┘     │
│            │                            │               │
│        PVC (1Gi)                    PVC (1Gi)           │
└─────────────────────────────────────────────────────────┘
```

**Goal**: postgres-0 is the single source of truth (primary). postgres-1 continuously copies all changes from postgres-0 (replica). If you write to the primary, the data appears on the replica within milliseconds.

---

## 1. ConfigMap — The Init Script

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: postgres-config
data:
  init-db.sh: |
    #!/bin/bash
    set -e
    ...
```

**What is this?** A ConfigMap stores configuration data as key-value pairs. Here, the key `init-db.sh` holds a shell script.

**Why a ConfigMap?** We need to run a script inside the container. Instead of baking it into a custom Docker image, we store it in Kubernetes and mount it as a file. This keeps things simple and changeable.

### Inside the init script

#### Step 1: Allow replication connections

```bash
echo "host replication replicator all md5" >> "$PGDATA/pg_hba.conf"
```

| Part | Meaning |
|------|---------|
| `pg_hba.conf` | PostgreSQL's access control file — decides **who** can connect and **how** |
| `host` | Allow TCP/IP connections (not just local socket) |
| `replication` | This is a special keyword — it means "allow replication connections" |
| `replicator` | Only the user named `replicator` can use this rule |
| `all` | From any IP address |
| `md5` | Require password authentication |

> **Analogy**: Think of `pg_hba.conf` as a bouncer list at a club door. This line adds: "Let `replicator` in from anywhere, if they know the password."

**Why is this needed?** Without this line, postgres-1 can't connect to postgres-0 for replication — the connection gets rejected.

#### Step 2: Create the replication user

```bash
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
  CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replpass';
  CREATE DATABASE userdb;
EOSQL
```

| Command | What it does |
|---------|-------------|
| `psql` | PostgreSQL command-line client |
| `-v ON_ERROR_STOP=1` | Stop immediately if any SQL fails |
| `--username "$POSTGRES_USER"` | Connect as the superuser (default: `postgres`) |
| `<<-EOSQL ... EOSQL` | Bash here-document — feeds multi-line SQL to psql |
| `CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replpass'` | Creates a user with the `REPLICATION` privilege |
| `CREATE DATABASE userdb` | Creates the application database |

> **Key point**: The `REPLICATION` privilege is a special PostgreSQL permission. A normal user can't replicate data — only a user with this privilege can.

#### Step 3: Create the app user and table

```bash
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d userdb <<-EOSQL
  CREATE ROLE appuser WITH LOGIN PASSWORD 'password123';
  CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  );
  GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO appuser;
  GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO appuser;
EOSQL
```

| Command | Why |
|---------|-----|
| `-d userdb` | Connect to the `userdb` database (not the default `postgres` db) |
| `CREATE ROLE appuser` | Separate user for the application — never use superuser in apps |
| `SERIAL PRIMARY KEY` | Auto-incrementing integer ID |
| `GRANT ALL ... ON ALL TABLES` | Let appuser read/write the `users` table |
| `GRANT USAGE, SELECT ON ALL SEQUENCES` | Let appuser use the auto-increment sequence (needed for INSERT with SERIAL) |

#### Step 4: Reload configuration

```bash
pg_ctl reload
```

This tells the running PostgreSQL server to re-read `pg_hba.conf` without restarting. The replication entry we added takes effect immediately.

---

## 2. Headless Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  clusterIP: None        # ← This makes it headless
  ports:
  - name: postgres
    port: 5432
  selector:
    app: postgres
```

### What makes it "headless"?

`clusterIP: None` — that's it. One field.

### What does it do?

| Regular Service | Headless Service |
|----------------|-----------------|
| Gets a ClusterIP (e.g., `10.96.x.x`) | No ClusterIP |
| `nslookup postgres` → returns 1 IP | `nslookup postgres` → returns **all pod IPs** |
| Load balances traffic | No load balancing |
| You can't reach a specific pod | Each pod gets its own DNS name |

### DNS names created

Because the StatefulSet uses `serviceName: postgres`, each pod gets a predictable DNS entry:

```
postgres-0.postgres.default.svc.cluster.local  →  10.244.2.7  (primary)
postgres-1.postgres.default.svc.cluster.local  →  10.244.1.7  (replica)
```

**Why this matters**: The write-app can connect to `postgres-0.postgres` to always hit the primary. The replica knows to connect to `postgres-0.postgres` for replication. These names are **stable** — they don't change even if pods restart.

---

## 3. ClusterIP Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres-read
spec:
  ports:
  - name: postgres
    port: 5432
  selector:
    app: postgres       # ← Same selector, matches BOTH pods
```

This is a regular ClusterIP service (the default type). It gets a single IP and load balances across all matching pods.

**Why?** The read-app doesn't care which database pod it talks to — both have the same data. Load balancing spreads the read load across both pods.

---

## 4. The StatefulSet

### Why StatefulSet and not Deployment?

| Feature | Deployment | StatefulSet |
|---------|-----------|-------------|
| Pod names | Random (e.g., `app-7f8b4d`) | Ordered (e.g., `postgres-0`, `postgres-1`) |
| Startup order | All at once | One by one, in order |
| Storage | Shared or none | Each pod gets its own PVC |
| DNS identity | None | `<pod>.<service>` via headless service |

Databases need all of these: predictable names, ordered startup (primary first, then replica), and dedicated storage per instance.

### Header

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
spec:
  serviceName: postgres      # Must match the headless service name
  replicas: 2                # postgres-0 and postgres-1
```

`serviceName: postgres` links the StatefulSet to the headless service. This is what creates the `postgres-0.postgres` DNS entries.

---

### Init Container — Role Detection & Cloning

The init container runs **before** the main PostgreSQL container starts. Its job: figure out if this pod is the primary or a replica, and prepare accordingly.

#### Detecting the role

```bash
[[ $HOSTNAME =~ -([0-9]+)$ ]] || exit 1
ordinal=${BASH_REMATCH[1]}
```

| Line | What it does |
|------|-------------|
| `$HOSTNAME` | In a StatefulSet, this is `postgres-0` or `postgres-1` |
| `=~ -([0-9]+)$` | Regex: extract the number at the end after the hyphen |
| `BASH_REMATCH[1]` | The captured number — `0` or `1` |

- `ordinal == 0` → this is the **primary**
- `ordinal != 0` → this is a **replica**

> **Why?** StatefulSet pods are created in order: postgres-0 first, then postgres-1. By convention, ordinal 0 is the primary.

#### Primary path (ordinal == 0)

```bash
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "Fresh primary — init script will run on first boot."
else
  echo "Data exists. Ensuring pg_hba has replication entry..."
  if ! grep -q "host replication replicator" "$PGDATA/pg_hba.conf"; then
    echo "host replication replicator all md5" >> "$PGDATA/pg_hba.conf"
  fi
fi
cp /config/init-db.sh /docker-entrypoint-initdb.d/init-db.sh
chmod +x /docker-entrypoint-initdb.d/init-db.sh
```

| Command | Why |
|---------|-----|
| `[ ! -s "$PGDATA/PG_VERSION" ]` | Checks if the data directory is empty. `-s` means "file exists and has size > 0". `PG_VERSION` is always present in an initialized PostgreSQL data directory. |
| `grep -q "host replication"` | Idempotent check — don't add the line if it already exists |
| `cp /config/init-db.sh /docker-entrypoint-initdb.d/` | Copies the init script from the ConfigMap mount to the docker entrypoint directory |

**How docker-entrypoint-initdb.d works**: The official PostgreSQL Docker image runs all `.sh` and `.sql` files in `/docker-entrypoint-initdb.d/` **only during first-time initialization** (when the data directory is empty). On subsequent restarts, these scripts are skipped.

#### Replica path (ordinal != 0)

```bash
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "Waiting for primary to accept connections..."
  until pg_isready -h postgres-0.postgres -U postgres; do sleep 2; done

  echo "Cloning data from primary using pg_basebackup..."
  rm -rf "$PGDATA"/*
  PGPASSWORD=replpass pg_basebackup \
    -h postgres-0.postgres \
    -U replicator \
    -D "$PGDATA" \
    -Fp -Xs -R
fi
```

Let's break down each command:

#### `pg_isready`

```bash
until pg_isready -h postgres-0.postgres -U postgres; do sleep 2; done
```

`pg_isready` is a utility that checks if PostgreSQL is accepting connections. Returns exit code 0 if yes.

| Flag | Meaning |
|------|---------|
| `-h postgres-0.postgres` | Host to check — the primary pod via headless service DNS |
| `-U postgres` | User to check connection as |

**Why?** The replica can't clone data until the primary is fully running. `until ... do sleep 2; done` retries every 2 seconds.

#### `pg_basebackup`

```bash
PGPASSWORD=replpass pg_basebackup \
  -h postgres-0.postgres \
  -U replicator \
  -D "$PGDATA" \
  -Fp -Xs -R
```

This is **the most important command for replication**. It creates a full copy of the primary's data directory.

| Flag | Meaning |
|------|---------|
| `PGPASSWORD=replpass` | Password for the `replicator` user (set as env var) |
| `-h postgres-0.postgres` | Connect to the primary via headless service |
| `-U replicator` | Connect as the replication user |
| `-D "$PGDATA"` | Where to write the cloned data |
| `-Fp` | **Format = plain** — write regular PostgreSQL files (not a tar archive) |
| `-Xs` | **WAL method = stream** — stream WAL (Write-Ahead Log) during backup to ensure consistency |
| `-R` | **Create standby.signal + primary_conninfo** — this is the magic flag |

#### What does the `-R` flag do?

The `-R` flag automatically creates two things in the cloned data directory:

1. **`standby.signal`** — an empty file. When PostgreSQL starts and sees this file, it knows: "I'm a replica, start in read-only standby mode."

2. **`primary_conninfo`** in `postgresql.auto.conf` — a connection string like:
   ```
   primary_conninfo = 'host=postgres-0.postgres user=replicator password=replpass'
   ```
   This tells the replica where to stream WAL changes from.

Without `-R`, you'd have to create both of these manually.

---

### Main Container — PostgreSQL Server

```yaml
containers:
- name: postgres
  image: postgres:15
  env:
  - name: POSTGRES_PASSWORD
    value: "postgres"
  - name: PGDATA
    value: /var/lib/postgresql/data/pgdata
  args:
  - postgres
  - -c
  - wal_level=replica
  - -c
  - max_wal_senders=3
  - -c
  - hot_standby=on
```

#### Environment Variables

| Variable | Purpose |
|----------|---------|
| `POSTGRES_PASSWORD` | Required by the Docker image — sets the superuser password |
| `PGDATA` | Where PostgreSQL stores its data. We use a subdirectory (`pgdata`) because the PVC mount point must not already contain files when mounted |

#### The `args` — PostgreSQL Runtime Configuration

The `args` override the container's default command. Here we pass `-c key=value` flags to configure PostgreSQL at startup:

| Setting | What it does | Why it's needed |
|---------|-------------|-----------------|
| `wal_level=replica` | Sets the level of WAL (Write-Ahead Log) detail. `replica` includes enough info for streaming replication | Without this, the WAL doesn't contain enough data for a replica to follow along |
| `max_wal_senders=3` | Maximum number of simultaneous replication connections | We have 1 replica, but setting 3 gives headroom for backups or additional replicas |
| `hot_standby=on` | Allows read queries on the replica while it's replicating | Without this, the replica would refuse all connections |

#### What is WAL (Write-Ahead Log)?

Think of WAL as a **transaction diary**. Before PostgreSQL changes any data, it first writes the change to the WAL. The replica reads this diary in real-time to apply the same changes to its copy.

```
Primary:  INSERT INTO users...  →  Write to WAL  →  Write to disk
                                        │
                                        ▼ (stream over network)
Replica:  Read WAL entry  →  Apply to local disk  →  Data is now replicated
```

---

### Health Probes

```yaml
readinessProbe:
  exec:
    command: ["pg_isready", "-U", "postgres"]
  initialDelaySeconds: 5
  periodSeconds: 5
livenessProbe:
  exec:
    command: ["pg_isready", "-U", "postgres"]
  initialDelaySeconds: 30
  periodSeconds: 10
```

| Probe | Question it answers | What happens if it fails |
|-------|--------------------|-----------------------|
| `readinessProbe` | "Is this pod ready to serve traffic?" | Pod is removed from Service endpoints — no traffic is sent to it |
| `livenessProbe` | "Is this pod still alive?" | Kubernetes restarts the container |

Both use `pg_isready`, which is the standard way to check if PostgreSQL is accepting connections. The liveness probe has a longer `initialDelaySeconds` (30s) because PostgreSQL needs time to start up — you don't want Kubernetes to kill it before it's had a chance to initialize.

---

### Volumes

```yaml
volumes:
- name: config
  configMap:
    name: postgres-config
    defaultMode: 0755
- name: initdb
  emptyDir: {}
```

| Volume | Type | Purpose |
|--------|------|---------|
| `config` | ConfigMap | Mounts `init-db.sh` from the ConfigMap as a file at `/config/` |
| `initdb` | emptyDir | Shared scratch space between init container and main container for `/docker-entrypoint-initdb.d/` |
| `data` | PVC (below) | Persistent storage for PostgreSQL data |

**Why `emptyDir` for initdb?** The init container needs to copy the init script to `/docker-entrypoint-initdb.d/`. The main container needs to read it from the same path. `emptyDir` is a temporary volume shared between all containers in the same pod.

**Why not mount the ConfigMap directly to `/docker-entrypoint-initdb.d/`?** ConfigMap mounts are read-only. The docker entrypoint might need to write marker files there. Also, we only want the init script on the primary, not the replica.

#### volumeClaimTemplates

```yaml
volumeClaimTemplates:
- metadata:
    name: data
  spec:
    accessModes: ["ReadWriteOnce"]
    resources:
      requests:
        storage: 1Gi
```

This is a StatefulSet-only feature. For each replica, Kubernetes automatically creates a separate PVC:
- `data-postgres-0` — storage for the primary
- `data-postgres-1` — storage for the replica

| Field | Meaning |
|-------|---------|
| `ReadWriteOnce` | The volume can be mounted by a single node for read/write |
| `storage: 1Gi` | Request 1 gigabyte of storage |

**Key behavior**: These PVCs are **not deleted** when the StatefulSet or pods are deleted. This means your data survives pod restarts and redeployments.

---

## How Replication Works End-to-End

Here's the complete flow when you deploy from scratch:

```
Step 1: postgres-0 starts
├── Init container: "I'm ordinal 0 → primary"
│   └── Copies init-db.sh to /docker-entrypoint-initdb.d/
├── Main container: PostgreSQL starts for the first time
│   ├── Runs initdb (creates empty database)
│   ├── Runs init-db.sh:
│   │   ├── Adds replication entry to pg_hba.conf
│   │   ├── Creates replicator user
│   │   ├── Creates userdb database
│   │   ├── Creates appuser and users table
│   │   └── Reloads config
│   └── Starts listening on port 5432
│
Step 2: postgres-1 starts (only after postgres-0 is ready)
├── Init container: "I'm ordinal 1 → replica"
│   ├── Waits for postgres-0 to be ready (pg_isready)
│   └── Runs pg_basebackup -R:
│       ├── Copies all data from postgres-0
│       ├── Creates standby.signal file
│       └── Writes primary_conninfo to postgresql.auto.conf
├── Main container: PostgreSQL starts
│   ├── Sees standby.signal → enters standby mode
│   ├── Reads primary_conninfo → connects to postgres-0
│   └── Starts streaming WAL changes in real-time
│
Step 3: Replication is active
├── Write to postgres-0 → data appears on postgres-1
└── postgres-1 is read-only (hot standby)
```

---

## PostgreSQL Commands Reference

Commands you can use to verify and troubleshoot the setup:

### Check replication status (run on primary)

```bash
kubectl exec postgres-0 -- psql -U postgres -c \
  "SELECT client_addr, state, sync_state FROM pg_stat_replication;"
```

Expected output:
```
 client_addr |   state   | sync_state
-------------+-----------+------------
 10.244.1.7  | streaming | async
```

- `streaming` = replica is actively receiving WAL
- `async` = asynchronous replication (primary doesn't wait for replica to confirm)

### Check if a pod is a replica (run on replica)

```bash
kubectl exec postgres-1 -- psql -U postgres -c "SELECT pg_is_in_recovery();"
```

- `t` (true) = this is a replica in recovery/standby mode
- `f` (false) = this is a primary

### Test replication end-to-end

```bash
# Write on primary
kubectl exec postgres-0 -- psql -U appuser -d userdb -c \
  "INSERT INTO users (name, email) VALUES ('Test', 'test@test.com');"

# Read from replica
kubectl exec postgres-1 -- psql -U appuser -d userdb -c \
  "SELECT * FROM users;"
```

### Check WAL status

```bash
# Current WAL position on primary
kubectl exec postgres-0 -- psql -U postgres -c "SELECT pg_current_wal_lsn();"

# Last WAL received by replica
kubectl exec postgres-1 -- psql -U postgres -c "SELECT pg_last_wal_receive_lsn();"
```

If both return the same value, the replica is fully caught up.

### Try writing to the replica (should fail)

```bash
kubectl exec postgres-1 -- psql -U appuser -d userdb -c \
  "INSERT INTO users (name, email) VALUES ('Fail', 'fail@test.com');"
```

Expected error: `ERROR: cannot execute INSERT in a read-only transaction`

This confirms the replica is properly read-only.
