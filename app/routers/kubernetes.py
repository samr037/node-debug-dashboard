from fastapi import APIRouter

from app.collectors.kubernetes import (
    collect_k8s_certificates,
    collect_k8s_components,
    collect_k8s_node_info,
    collect_kubernetes,
)
from app.models.kubernetes import (
    CertificateInfo,
    K8sComponentStatus,
    K8sNodeInfo,
    KubernetesOverview,
)

router = APIRouter(tags=["kubernetes"])


@router.get("", response_model=KubernetesOverview)
async def get_kubernetes():
    """Full Kubernetes overview: node info, certificates, components, API endpoint."""
    return await collect_kubernetes()


@router.get("/node-info", response_model=K8sNodeInfo)
async def get_node_info():
    """Kubernetes node labels, conditions, addresses, capacity, and allocatable."""
    return await collect_k8s_node_info()


@router.get("/certificates", response_model=list[CertificateInfo])
async def get_certificates():
    """Kubernetes PKI certificates with expiry status."""
    return await collect_k8s_certificates()


@router.get("/components", response_model=list[K8sComponentStatus])
async def get_components():
    """Health status of core Kubernetes components on this node."""
    return await collect_k8s_components()
