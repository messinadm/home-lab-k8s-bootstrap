import pulumi
import pulumi_command as command
import pulumi_kubernetes as k8s

# Configuration
k3s_version = "v1.28.5+k3s1"
k3s_options = [
    "--disable=traefik",  # We'll use our own ingress later
    "--write-kubeconfig-mode=644",  # Readable kubeconfig
]

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
    triggers=[k3s_version],  # Enables upgrades
    opts=pulumi.ResourceOptions(depends_on=[check_k3s])
)

# 3. Setup kubeconfig access
setup_kubeconfig = command.local.Command(
    "setup-kubeconfig",
    create="""
        mkdir -p $HOME/.kube && \
        sudo cp /etc/rancher/k3s/k3s.yaml $HOME/.kube/config && \
        sudo chown $USER:$USER $HOME/.kube/config
    """,
    opts=pulumi.ResourceOptions(depends_on=[install_k3s])
)

# 4. Wait for k3s to be ready
wait_for_k3s = command.local.Command(
    "wait-for-k3s",
    create="kubectl wait --for=condition=ready node --all --timeout=60s",
    opts=pulumi.ResourceOptions(depends_on=[setup_kubeconfig])
)

# LAYER 2: Kubernetes Resources via Kubernetes Provider

# Configure K8s provider to use our k3s cluster
k8s_provider = k8s.Provider(
    "k3s",
    kubeconfig=pulumi.Output.secret("/home/sunnydmess/.kube/config"),
    opts=pulumi.ResourceOptions(depends_on=[wait_for_k3s])
)

# Create media namespace
media_namespace = k8s.core.v1.Namespace(
    "media-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name="media"),
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

# Create PersistentVolumes (hostPath for now, NFS later)
jellyfin_config_pv = k8s.core.v1.PersistentVolume(
    "jellyfin-config-pv",
    metadata=k8s.meta.v1.ObjectMetaArgs(name="jellyfin-config-pv"),
    spec=k8s.core.v1.PersistentVolumeSpecArgs(
        capacity={"storage": "10Gi"},
        access_modes=["ReadWriteOnce"],
        host_path=k8s.core.v1.HostPathVolumeSourceArgs(
            path="/data/jellyfin/config",
            type="DirectoryOrCreate",
        ),
        storage_class_name="local-storage",
    ),
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

jellyfin_media_pv = k8s.core.v1.PersistentVolume(
    "jellyfin-media-pv",
    metadata=k8s.meta.v1.ObjectMetaArgs(name="jellyfin-media-pv"),
    spec=k8s.core.v1.PersistentVolumeSpecArgs(
        capacity={"storage": "500Gi"},
        access_modes=["ReadWriteMany"],  # Future NFS compatibility
        host_path=k8s.core.v1.HostPathVolumeSourceArgs(
            path="/data/jellyfin/media",
            type="DirectoryOrCreate",
        ),
        storage_class_name="local-storage",
    ),
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

# Export useful values
pulumi.export("k3s_version", k3s_version)
pulumi.export("media_namespace", media_namespace.metadata["name"])
pulumi.export("kubeconfig_path", "$HOME/.kube/config")
