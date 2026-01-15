# Home Lab Kubernetes Bootstrap

Pulumi-based infrastructure as code for home lab k3s cluster with ArgoCD GitOps.

## What This Does

Automates the complete infrastructure setup:

```
sudo -E pulumi up
    ↓
1. Install/configure k3s (Kubernetes)
2. Install NVIDIA Container Toolkit (GPU support)
3. Bootstrap ArgoCD (GitOps controller)
4. Create namespaces (media, etc.)
5. Create PersistentVolumes (storage)
    ↓
ArgoCD takes over → Deploys applications from home-lab-gitops repo
```

## Architecture

**Two-Layer Approach**:
1. **Infrastructure Layer** (Command provider): Manages k3s, NVIDIA toolkit - runs locally with sudo
2. **Application Layer** (Kubernetes provider): Creates K8s resources - interacts with cluster API

**Benefits**:
- Full automation: One command installs everything
- Idempotent: Safe to run multiple times
- Version controlled: k3s version in code
- Security: No privileged pods, clean separation

## Prerequisites

### System Requirements
- **OS**: Pop!_OS 22.04 (or Ubuntu-based)
- **GPU**: NVIDIA GPU (for Jellyfin transcoding)
- **Disk**: Space for `/data/jellyfin` (10Gi config + 500Gi media)

### Software Requirements
1. **Pulumi** - Install from https://www.pulumi.com/docs/install/
2. **Python 3.8+** with `uv` - Will be set up by Pulumi
3. **sudo access** - Required for k3s and NVIDIA toolkit installation

## Setup

### 1. Clone Repository
```bash
cd ~/workspace
git clone https://github.com/messinadm/home-lab-k8s-bootstrap.git
cd home-lab-k8s-bootstrap
```

### 2. Login to Pulumi

**Option A: Pulumi Cloud (recommended for beginners)**
```bash
pulumi login
# Opens browser to create free account
```

**Option B: Local File Backend (no account needed)**
```bash
pulumi login file://~/.pulumi
# Stores state locally in ~/.pulumi
```

### 3. Initialize Stack
```bash
pulumi stack init dev
# Or: pulumi stack init prod
```

### 4. Deploy Infrastructure
```bash
sudo -E pulumi up
```

**Why `sudo -E`?**
- `sudo`: Required for k3s installation, NVIDIA toolkit, systemd operations
- `-E`: Preserves environment variables (Pulumi tokens, user home directory)

### 5. Verify Deployment
```bash
# Check k3s
kubectl cluster-info
kubectl get nodes

# Check ArgoCD
kubectl get pods -n argocd

# Check GPU availability
kubectl describe nodes | grep -A 10 "Allocated resources"
# Should show: nvidia.com/gpu
```

## Configuration

### k3s Version Upgrade
Edit `__main__.py`:
```python
k3s_version = "v1.28.5+k3s1"  # Change to desired version
```

Run:
```bash
sudo -E pulumi up
```

Pulumi will detect the change and upgrade k3s.

### Storage Paths
Storage is created at:
- `/data/jellyfin/config` - Jellyfin configuration (10Gi)
- `/data/jellyfin/media` - Recorded TV shows (500Gi)

To change paths, edit `__main__.py`:
```python
host_path=k8s.core.v1.HostPathVolumeSourceArgs(
    path="/your/custom/path",
    type="DirectoryOrCreate",
)
```

### GitOps Repository
By default, points to local clone:
```python
gitops_repo_path = "/home/sunnydmess/workspace/home-lab-gitops"
```

Update if your path differs.

## Components

### What Gets Installed

1. **k3s** (v1.28.5+k3s1)
   - Lightweight Kubernetes
   - Traefik disabled (for custom ingress later)
   - Writable kubeconfig at `~/.kube/config`

2. **NVIDIA Container Toolkit**
   - From Pop!_OS repositories
   - Configures containerd for GPU access
   - Enables GPU scheduling in Kubernetes

3. **ArgoCD**
   - Deployed via Kustomize from local GitOps repo
   - Overlay: `argocd/pop-os.433palmetto.com/bootstrap/overlays/pop-os.433palmetto.com`
   - Manages all application deployments

4. **Namespaces**
   - `argocd` - GitOps controller
   - `media` - Jellyfin and media applications

5. **PersistentVolumes**
   - `jellyfin-config-pv` - 10Gi hostPath for Jellyfin config
   - `jellyfin-media-pv` - 500Gi hostPath for recordings

## Outputs

After successful deployment, Pulumi exports:

```bash
pulumi stack output

Outputs:
  argocd_admin_password_cmd: "kubectl -n argocd get secret..."
  argocd_namespace: "argocd"
  k3s_version: "v1.28.5+k3s1"
  kubeconfig_path: "$HOME/.kube/config"
  media_namespace: "media"
```

### Get ArgoCD Admin Password
```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
```

## Troubleshooting

### k3s Installation Fails
```bash
# Check if k3s service exists
sudo systemctl status k3s

# View installation logs
sudo journalctl -u k3s -n 50

# Manual cleanup if needed
sudo /usr/local/bin/k3s-uninstall.sh
```

### NVIDIA GPU Not Available

**Common Issues and Solutions:**

1. **Driver/Library Version Mismatch**
   ```bash
   # Check for version mismatch
   nvidia-smi
   # If you see "Failed to initialize NVML: Driver/library version mismatch"
   # Solution: Reboot to load matching kernel module
   sudo reboot
   ```

2. **Device Plugin Not Detecting GPU**
   ```bash
   # Check device plugin logs
   kubectl logs -n kube-system -l name=nvidia-device-plugin-ds

   # If you see "Incompatible strategy detected auto", redeploy with NVML strategy:
   kubectl delete daemonset nvidia-device-plugin-daemonset -n kube-system
   # Then apply the device plugin with DEVICE_DISCOVERY_STRATEGY=nvml
   ```

3. **RuntimeClass Not Found**
   ```bash
   # Verify nvidia RuntimeClass exists
   kubectl get runtimeclass nvidia

   # k3s auto-creates this when it detects nvidia-container-runtime
   # If missing, restart k3s:
   sudo systemctl restart k3s
   ```

4. **General Checks**
   ```bash
   # Check if toolkit is installed
   dpkg -l | grep nvidia-container-toolkit

   # Check containerd config for nvidia runtime
   sudo grep nvidia /var/lib/rancher/k3s/agent/etc/containerd/config.toml

   # Verify GPU is visible to Kubernetes
   kubectl describe nodes | grep nvidia.com/gpu

   # Test GPU access in a pod
   kubectl run gpu-test --image=nvidia/cuda:12.2.0-base-ubuntu22.04 \
     --restart=Never --rm -it \
     --overrides='{"spec":{"runtimeClassName":"nvidia"}}' \
     -- nvidia-smi
   ```

### ArgoCD Not Starting
```bash
# Check ArgoCD pods
kubectl get pods -n argocd

# View logs
kubectl logs -n argocd -l app.kubernetes.io/name=argocd-server

# Manually apply if needed
kubectl apply -k /home/sunnydmess/workspace/home-lab-gitops/argocd/pop-os.433palmetto.com/bootstrap/overlays/pop-os.433palmetto.com
```

### Pulumi State Issues
```bash
# View current state
pulumi stack

# Refresh state from actual cluster
pulumi refresh

# Export state (backup)
pulumi stack export > backup.json
```

## Maintenance

### Upgrade k3s
1. Update `k3s_version` in `__main__.py`
2. Run: `sudo -E pulumi up`
3. Verify: `kubectl version`

### Upgrade ArgoCD
ArgoCD version is pulled from official manifests:
```yaml
# In GitOps repo: argocd/.../bootstrap/base/kustomization.yaml
resources:
- https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

To upgrade, change `stable` to specific version tag.

### Add More Storage
Edit `__main__.py` to add new PersistentVolumes:
```python
new_pv = k8s.core.v1.PersistentVolume(
    "my-app-pv",
    metadata=k8s.meta.v1.ObjectMetaArgs(name="my-app-pv"),
    spec=k8s.core.v1.PersistentVolumeSpecArgs(
        capacity={"storage": "100Gi"},
        access_modes=["ReadWriteOnce"],
        host_path=k8s.core.v1.HostPathVolumeSourceArgs(
            path="/data/my-app",
            type="DirectoryOrCreate",
        ),
    ),
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)
```

## Related Repositories

- **GitOps**: https://github.com/sunnydmess/home-lab-gitops
  - ArgoCD configuration
  - Application manifests (Jellyfin, etc.)
  - Deployed automatically after this bootstrap

## Learning Resources

This project demonstrates:
- **Pulumi** - Infrastructure as Code with Python
- **k3s** - Lightweight Kubernetes for home labs
- **GitOps** - Declarative application deployment
- **GPU Scheduling** - Kubernetes + NVIDIA integration
- **Two-layer architecture** - Command provider (host) + K8s provider (cluster)

## License

MIT
