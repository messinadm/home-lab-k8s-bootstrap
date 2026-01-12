import pulumi
import pulumi_command as command
import pulumi_kubernetes as k8s

# Configuration
k3s_version = "v1.35.0+k3s1"  # Latest stable (Kubernetes 1.35)
k3s_options = [
    "--disable=traefik",  # We'll use our own ingress later
    "--write-kubeconfig-mode=644",  # Readable kubeconfig
]
gitops_repo_path = "/home/sunnydmess/workspace/home-lab-gitops"
argocd_overlay = f"{gitops_repo_path}/argocd/pop-os.433palmetto.com/bootstrap/overlays/pop-os.433palmetto.com"

# LAYER 1: k3s Management via Command Provider

# 1. Check if k3s is installed
check_k3s = command.local.Command(
    "check-k3s",
    create="which k3s || echo 'not-installed'",
    opts=pulumi.ResourceOptions(delete_before_replace=True)
)

# 2. Install k3s if needed (idempotent)
install_k3s = command.local.Command(
    "install-k3s",
    create=f"curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION={k3s_version} sh -s - {' '.join(k3s_options)}",
    delete="sudo /usr/local/bin/k3s-uninstall.sh",
    triggers=[k3s_version],  # Enables upgrades
    opts=pulumi.ResourceOptions(depends_on=[check_k3s])
)

# 3. Setup kubeconfig access
# Note: NVIDIA drivers and toolkit managed by Pop!_OS (system packages)
# GPU Operator (deployed via ArgoCD) handles Kubernetes GPU integration
setup_kubeconfig = command.local.Command(
    "setup-kubeconfig",
    create="""
        mkdir -p $HOME/.kube && \
        sudo cp /etc/rancher/k3s/k3s.yaml $HOME/.kube/config && \
        sudo chown $USER:$USER $HOME/.kube/config
    """,
    delete="rm -f $HOME/.kube/config",
    opts=pulumi.ResourceOptions(depends_on=[install_k3s])
)

# 3.5. Ensure containerd uses k3s's CNI bin path
# GPU Operator may modify this to /opt/cni/bin, so enforce correct path
fix_containerd_cni_path = command.local.Command(
    "fix-containerd-cni-path",
    create="""
        sudo sed -i 's|/opt/cni/bin|/var/lib/rancher/k3s/data/current/bin|g' /var/lib/rancher/k3s/agent/etc/containerd/config.toml && \
        sudo systemctl restart k3s
    """,
    opts=pulumi.ResourceOptions(depends_on=[setup_kubeconfig])
)

# 4. Wait for k3s to be ready
wait_for_k3s = command.local.Command(
    "wait-for-k3s",
    create="kubectl wait --for=condition=ready node --all --timeout=60s",
    opts=pulumi.ResourceOptions(depends_on=[fix_containerd_cni_path])
)

# LAYER 2: Kubernetes Resources via Kubernetes Provider

# Configure K8s provider to use our k3s cluster
k8s_provider = k8s.Provider(
    "k3s",
    kubeconfig=pulumi.Output.secret("/home/sunnydmess/.kube/config"),
    opts=pulumi.ResourceOptions(depends_on=[wait_for_k3s])
)

# Create ArgoCD namespace
argocd_namespace = k8s.core.v1.Namespace(
    "argocd-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name="argocd"),
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

# Read SSH private key for GitOps repository
with open("/home/sunnydmess/.ssh/home-lab-gitops_ed25519", "r") as f:
    ssh_private_key = f.read()

# Create ArgoCD repository secret for GitOps access
argocd_repo_secret = k8s.core.v1.Secret(
    "argocd-gitops-repo-secret",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="home-lab-gitops-repo",
        namespace="argocd",
        labels={
            "argocd.argoproj.io/secret-type": "repository"
        }
    ),
    string_data={
        "type": "git",
        "url": "git@github.com:sunnydmess/home-lab-gitops.git",
        "sshPrivateKey": ssh_private_key
    },
    opts=pulumi.ResourceOptions(
        provider=k8s_provider,
        depends_on=[argocd_namespace]
    )
)

# Install ArgoCD CRDs first (needed before Application resources)
install_argocd_crds = command.local.Command(
    "install-argocd-crds",
    create="kubectl apply -k https://github.com/argoproj/argo-cd/manifests/crds\\?ref\\=stable",
    opts=pulumi.ResourceOptions(depends_on=[argocd_repo_secret])
)

# Wait for CRDs to be established
wait_for_crds = command.local.Command(
    "wait-for-argocd-crds",
    create="kubectl wait --for condition=established --timeout=60s crd/applications.argoproj.io crd/applicationsets.argoproj.io crd/appprojects.argoproj.io",
    opts=pulumi.ResourceOptions(depends_on=[install_argocd_crds])
)

# Bootstrap ArgoCD via Kustomize
bootstrap_argocd = command.local.Command(
    "bootstrap-argocd",
    create=f"kubectl apply -k {argocd_overlay}",
    delete="""
        kubectl delete applications --all -n argocd --ignore-not-found=true --wait=false ; \
        kubectl delete applicationsets --all -n argocd --ignore-not-found=true --wait=false ; \
        kubectl delete -k """ + argocd_overlay + """ --ignore-not-found=true ; \
        exit 0
    """,
    opts=pulumi.ResourceOptions(depends_on=[wait_for_crds])
)

# Wait for ArgoCD to be ready
wait_for_argocd = command.local.Command(
    "wait-for-argocd",
    create="kubectl wait --for=condition=available --timeout=300s deployment/argocd-server -n argocd",
    opts=pulumi.ResourceOptions(depends_on=[bootstrap_argocd])
)

# Create media namespace
media_namespace = k8s.core.v1.Namespace(
    "media-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name="media"),
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

# Note: Using k3s's built-in local-path storage class for dynamic provisioning
# PVCs are created by the Jellyfin Helm chart, PVs are auto-provisioned by local-path

# Note: NVIDIA GPU support is handled by GPU Operator (deployed via ArgoCD)
# GPU Operator manages device plugin, runtime configuration, and GPU feature discovery

# Export useful values
pulumi.export("k3s_version", k3s_version)
pulumi.export("argocd_namespace", "argocd")
pulumi.export("media_namespace", media_namespace.metadata["name"])
pulumi.export("kubeconfig_path", "$HOME/.kube/config")
pulumi.export("argocd_admin_password_cmd", "kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d")
