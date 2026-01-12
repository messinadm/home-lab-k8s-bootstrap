import pulumi
import pulumi_command as command
import pulumi_kubernetes as k8s
import os

# Configuration
config = pulumi.Config()
username = config.get("username") or os.getenv("SUDO_USER") or os.getenv("USER")
home_dir = f"/home/{username}"
server_name = config.get("server_name") or "pop-os.433palmetto.com"

k3s_version = "v1.35.0+k3s1"  # Latest stable (Kubernetes 1.35)
k3s_options = [
    "--disable=traefik",  # We'll use our own ingress later
    "--write-kubeconfig-mode=644",  # Readable kubeconfig
]
gitops_repo_path = config.get("gitops_repo_path") or f"{home_dir}/workspace/home-lab-gitops"
argocd_overlay = f"{gitops_repo_path}/argocd/{server_name}/bootstrap/overlays/{server_name}"
ssh_key_path = config.get("ssh_key_path") or f"{home_dir}/.ssh/home-lab-gitops_ed25519"
kubeconfig_path = f"{home_dir}/.kube/config"

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
setup_kubeconfig = command.local.Command(
    "setup-kubeconfig",
    create=f"""
        mkdir -p {home_dir}/.kube && \
        cp /etc/rancher/k3s/k3s.yaml {kubeconfig_path} && \
        chown {username}:{username} {kubeconfig_path} && \
        chmod 600 {kubeconfig_path}
    """,
    triggers=[install_k3s.stdout],  # Re-run when k3s is reinstalled
    opts=pulumi.ResourceOptions(depends_on=[install_k3s])
)

# Wait for k3s to be ready
wait_for_k3s = command.local.Command(
    "wait-for-k3s",
    create="""
        for i in {1..60}; do
            if kubectl get nodes &>/dev/null; then
                kubectl wait --for=condition=ready node --all --timeout=60s && break
            fi
            sleep 1
        done
    """,
    opts=pulumi.ResourceOptions(depends_on=[setup_kubeconfig])
)

# LAYER 2: Kubernetes Resources via Kubernetes Provider

# Configure K8s provider to use our k3s cluster
k8s_provider = k8s.Provider(
    "k3s",
    kubeconfig=pulumi.Output.secret(kubeconfig_path),
    opts=pulumi.ResourceOptions(depends_on=[wait_for_k3s])
)

# Create ArgoCD namespace (using K8s provider for drift detection)
argocd_namespace = k8s.core.v1.Namespace(
    "argocd-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name="argocd"),
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

# Read SSH private key for GitOps repository
with open(ssh_key_path, "r") as f:
    ssh_private_key = f.read()

# Create ArgoCD repository secret for GitOps access (using K8s provider for drift detection)
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
# Trigger on namespace ID so it re-runs after destroy/recreate
# (namespace gets new UID when cluster is destroyed and rebuilt)
bootstrap_argocd = command.local.Command(
    "bootstrap-argocd",
    create=f"kubectl apply -k {argocd_overlay}",
    triggers=[argocd_namespace.id],  # Re-run when namespace is recreated (new UID after destroy)
    opts=pulumi.ResourceOptions(depends_on=[wait_for_crds, argocd_namespace, argocd_repo_secret])
)

# Wait for ArgoCD to be ready
wait_for_argocd = command.local.Command(
    "wait-for-argocd",
    create="kubectl wait --for=condition=available --timeout=300s deployment/argocd-server -n argocd",
    opts=pulumi.ResourceOptions(depends_on=[bootstrap_argocd])
)

# Create media namespace (using K8s provider for drift detection)
media_namespace = k8s.core.v1.Namespace(
    "media-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name="media"),
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

# Note: Using k3s's built-in local-path storage class for dynamic provisioning
# PVCs are created by the Jellyfin Helm chart, PVs are auto-provisioned by local-path

# Note: NVIDIA GPU support handled by k3s native runtime detection
# k3s automatically detects nvidia-container-runtime if installed on the host

# Export useful values
pulumi.export("k3s_version", k3s_version)
pulumi.export("username", username)
pulumi.export("server_name", server_name)
pulumi.export("argocd_namespace", "argocd")
pulumi.export("media_namespace", media_namespace.metadata["name"])
pulumi.export("kubeconfig_path", kubeconfig_path)
pulumi.export("argocd_admin_password_cmd", "kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d")
