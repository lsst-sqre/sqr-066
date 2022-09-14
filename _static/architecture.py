"""Source for architecture.png component diagram."""

import os

from diagrams import Cluster, Diagram
from diagrams.gcp.compute import KubernetesEngine
from diagrams.gcp.network import LoadBalancing
from diagrams.k8s.compute import Deployment, Pod, ReplicaSet
from diagrams.k8s.group import Namespace
from diagrams.k8s.podconfig import ConfigMap, Secret
from diagrams.onprem.client import User

os.chdir(os.path.dirname(__file__))

graph_attr = {
    "label": "",
    "labelloc": "bbc",
    "nodesep": "0.2",
    "pad": "0.2",
    "ranksep": "0.75",
    "splines": "splines",
}

node_attr = {
    "fontsize": "12.0",
}

with Diagram(
    "Notebook Aspect spawner architecture",
    show=False,
    filename="architecture",
    outformat="png",
    graph_attr=graph_attr,
    node_attr=node_attr,
):
    user = User("End user")

    with Cluster("Kubernetes"):
        ingress = LoadBalancing("Ingress")
        gafaelfawr = KubernetesEngine("Gafaelfawr")

        with Cluster("JupyterHub"):
            jupyterproxy = KubernetesEngine("JupyterHub proxy")
            jupyterhub = KubernetesEngine("JupyterHub\nw/APISpawner")
            jupyterhub_token = Secret("JupyterHub token")

        with Cluster("Spawner"):
            spawner = KubernetesEngine("Spawner")
            image_puller = ReplicaSet("Image prepuller")

        with Cluster("User namespace"):
            namespace = Namespace("User")
            pod = Pod("Lab pod")
            configmap = ConfigMap("Lab configuration")
            token = Secret("User notebook token")
            secrets = Secret("Other lab secrets")

    user >> ingress >> jupyterproxy >> jupyterhub
    ingress >> gafaelfawr
    jupyterhub << jupyterhub_token
    jupyterhub >> spawner
    gafaelfawr << spawner
    spawner >> image_puller
    jupyterproxy >> pod
    spawner >> namespace
    namespace - [configmap, token, secrets] - pod
