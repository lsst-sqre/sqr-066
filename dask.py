from diagrams import Cluster
from diagrams.k8s.compute import Deployment, Pod
from diagrams.programming.framework import FastAPI
# from diagrams.custom import Custom
# from diagrams.generic.blank import Blank
from diagrams.generic.storage import Storage
from sphinx_diagrams import SphinxDiagram

with SphinxDiagram(title="Labs creating Dask via Spawning Service"):
    with Cluster("Rubin Science Platform Kubernetes Cluster"):
        with Cluster("Namespace Spawningservice"):
            spawningservice = Deployment("spawningservice")
            
        with Cluster("Namespace User 1"):
            lab=Pod("User 1 Lab")
            dasks=[Pod(f"User 1 Dask {i + 1}") for i in range(3)]

    lab >> spawningservice 
    spawningservice >> dasks
