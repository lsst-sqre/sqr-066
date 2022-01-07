from diagrams import Cluster
from diagrams.k8s.compute import Deployment, Pod
from diagrams.programming.framework import FastAPI
# from diagrams.custom import Custom
# from diagrams.generic.blank import Blank
from diagrams.generic.storage import Storage
from sphinx_diagrams import SphinxDiagram

with SphinxDiagram(title="Hub spawning via Spawning Service"):
    with Cluster("Rubin Science Platform Kubernetes Cluster"):

        with Cluster("Namespace Nublado2"):
            jupyterhub = Deployment("jupyterhub")

        with Cluster("Namespace Spawningservice"):
            spawningservice = Deployment("spawningservice")
            
        with Cluster("User Namespaces"):
            n3=Cluster("User 3")

            with Cluster("Namespace User 1"):
                lab1 = Pod("User 1 Lab")
            with Cluster("Namespace User 2"):
                lab2 = Pod("User 2 Lab")
            with Cluster("Namespace User 3"):
                lab3 = Pod("User 3 Lab")

            labs=[lab1,lab2,lab3]

    jupyterhub >> spawningservice
    spawningservice >> labs
