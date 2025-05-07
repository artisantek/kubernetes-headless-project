# Understanding Kubernetes Headless Services: MySQL Replication Demo

## Introduction
This project demonstrates the practical implementation of Kubernetes Headless Services using a real-world example of MySQL master-slave replication. The demo consists of three main components: a Write Application, a Read Application, and a MySQL database setup with master-slave replication.

## What is a Headless Service?
A Headless Service in Kubernetes is a service that doesn't allocate a cluster IP address. Instead, it creates DNS entries for each Pod that's part of the service. This is particularly useful when:

- You need direct communication with specific Pods
- You don't need load balancing
- You want to discover individual Pod IP addresses

Key characteristics of a Headless Service:
- The `clusterIP` field is set to `None`
- DNS queries return the IP addresses of all Pods that match the service selector
- Each Pod gets its own DNS entry in the format: `<pod-name>.<service-name>.<namespace>.svc.cluster.local`

## Demo Overview
This project demonstrates Headless Services using a practical MySQL master-slave replication setup. By using this common database pattern, we'll see how Headless Services enable direct Pod communication.

### Why MySQL Replication as an Example?
- **Direct Communication Need**: Write operations must go to the master node specifically
- **DNS-Based Discovery**: Slave nodes need to find and connect to the master
- **Stable Network Identity**: Each MySQL instance needs a consistent identity for replication

## Architecture Overview

<div align="center">
  <img src="./images/architecture.jpg" width="500" alt="MySQL Master-Slave Architecture with Kubernetes Services">
</div>

### Components

#### MySQL StatefulSet
* 2 Pod replicas (1 master, 1 slave)
* mysql-0.mysql: Master node (Primary)
* mysql-1.mysql: Slave node (Replica)

#### Kubernetes Services
* Headless Service (mysql): For direct Pod access
* ClusterIP Service (mysql-read): For read operations load balancing

#### Applications
* Write App: Connects directly to master (mysql-0.mysql)
* Read App: Connects to mysql-read service

### Service Discovery

#### Headless Service (mysql)
* DNS entries:
  * mysql-0.mysql.default.svc.cluster.local
  * mysql-1.mysql.default.svc.cluster.local
* Used by Write App to connect to master
* No load balancing (clusterIP: None)

#### Read Service (mysql-read)
* Regular ClusterIP service
* Load balances read requests across both Pods
* Used by Read App for distributed reading

## Components Details

### MySQL Database
* StatefulSet with 2 replicas
* Persistent storage: 1Gi per pod
* Master node configuration:
  * Binary logging enabled
  * Handles all write operations
* Slave node configuration:
  * Super-read-only mode
  * Automatic replication from master

### Write Application (Port 30000)
* Connects directly to MySQL master via headless service
* Features:
  * User registration interface
  * Database initialization
* Environment Configuration:
  * DB_HOST: mysql-0.mysql
  * DB_USER: appuser
  * DB_PASSWORD: password123
  * DB_NAME: userdb

### Read Application (Port 30001)
* Connects to mysql-read service for load balanced reads
* Features:
  * MySQL pod identification
  * Load balancing demonstration
* Environment Configuration:
  * DB_HOST: mysql-read
  * DB_USER: appuser
  * DB_PASSWORD: password123
  * DB_NAME: userdb

## Setting Up Dynamic Volume Provisioning

For the MySQL StatefulSet to work properly, you need a functioning storage class for dynamic volume provisioning. This demo uses Persistent Volume Claims (PVCs) which require either manually created Persistent Volumes or a storage provisioner.

### Using OpenEBS for Local Storage

OpenEBS provides an easy way to set up dynamic volume provisioning for local volumes, which is perfect for development and testing environments.

#### Install OpenEBS

1. Add the OpenEBS Helm repository:
```bash
helm repo add openebs https://openebs.github.io/charts
helm repo update
```

2. Install OpenEBS (without Mayastor for simplicity):
```bash
helm install openebs --namespace openebs openebs/openebs \
  --set engines.replicated.mayastor.enabled=false \
  --create-namespace
```

3. Verify the installation:
```bash
kubectl get pods -n openebs
```

#### Set OpenEBS hostpath as Default Storage Class

1. Check your current storage classes:
```bash
kubectl get storageclass
```

2. Make OpenEBS hostpath the default storage class:
```bash
# First, unset the default flag on your current default storage class (if any)
kubectl patch storageclass <current-default-storage-class> -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"false"}}}'

# Then set OpenEBS hostpath as the default
kubectl patch storageclass openebs-hostpath -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
```

3. Verify the changes:
```bash
kubectl get storageclass
```
You should see openebs-hostpath marked as default (with "(default)" next to its name).

4. Update your PVCs to use the new storage class:
If you've already defined PVCs in your YAML files, you can either:
- Remove the `storageClassName` field to use the default
- Explicitly set `storageClassName: openebs-hostpath`

### Alternative Options

If you're running in a cloud environment:
- AWS: Use `aws-ebs` storage class
- GCP: Use `standard` storage class
- Azure: Use `managed-premium` storage class

For production use cases, consider using more robust storage solutions like Ceph, Portworx, or cloud-native volume solutions.

## Quick Start

### Prerequisites
* Kubernetes cluster
* kubectl configured
* Node ports 30000 and 30001 available
* Storage class configured (see Dynamic Volume Provisioning section)

### Directory Structure
```
/  
├── kubernetes/  
│   ├── mysql-statefulset.yaml  
│   ├── write-app.yaml  
│   └── read-app.yaml  
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

### Installation Steps

1. Clone the repository:
```bash
git clone https://github.com/artisantek/kubernetes-headless-project.git
cd kubernetes-headless-project
```

2. Deploy MySQL Cluster:
```bash
kubectl apply -f kubernetes/mysql-statefulset.yaml
```

3. Deploy Applications:
```bash
kubectl apply -f kubernetes/write-app.yaml
kubectl apply -f kubernetes/read-app.yaml
```

4. Verify all pods are running:
```bash
kubectl get pods
```

## Understanding the Demo

### 1. Observing DNS Resolution
```bash
# Check DNS entries created by headless service
kubectl run -it --rm debug --image=busybox -- nslookup mysql
kubectl run -it --rm debug --image=busybox -- nslookup mysql-0.mysql
```

### 2. Testing Direct Communication
- Access Write App: `http://<node-ip>:30000`
  - Writes always go to mysql-0 (master) via headless service
- Access Read App: `http://<node-ip>:30001`
  - Reads are load balanced across pods

### Verify Replication
```bash
# Connect to master (mysql-0)
kubectl exec -it mysql-0 -- mysql -u root -e "SHOW SLAVE HOSTS;"

# Connect to slave (mysql-1)
kubectl exec -it mysql-1 -- mysql -u root -e "SHOW SLAVE STATUS\G"
```

The slave status should show:

* Slave_IO_Running: Yes
* Slave_SQL_Running: Yes

## Key Learning Points
1. **Headless vs Regular Services**
   - Headless: Direct Pod access, no load balancing
   - ClusterIP: Load balanced, single service IP

2. **DNS Resolution**
   - Headless creates individual Pod DNS entries
   - Regular creates single service DNS entry

3. **Use Cases**
   - When to use headless services
   - When to use regular services
   - How to combine both in a single application

## Production Notes
This is a demo environment. For production:
- Use proper secrets for MySQL credentials
- Configure appropriate storage classes
- Implement proper backup strategies