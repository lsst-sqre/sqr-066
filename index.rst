..
  Technote content.

  See https://developer.lsst.io/restructuredtext/style.html
  for a guide to reStructuredText writing.

  Do not put the title, authors or other metadata in this document;
  those are automatically added.

  Use the following syntax for sections:

  Sections
  ========

  and

  Subsections
  -----------

  and

  Subsubsections
  ^^^^^^^^^^^^^^

  To add images, add the image file (png, svg or jpeg preferred) to the
  _static/ directory. The reST syntax for adding the image is

  .. figure:: /_static/filename.ext
     :name: fig-label

     Caption text.

   Run: ``make html`` and ``open _build/html/index.html`` to preview your work.
   See the README at https://github.com/lsst-sqre/lsst-technote-bootstrap or
   this repo's README for more info.

   Feel free to delete this instructional comment.

:tocdepth: 1

.. Please do not modify tocdepth; will be fixed when a new Sphinx theme is shipped.

.. sectnum::

.. TODO: Delete the note below before merging new content to the main branch.

.. note::

   **This technote is not yet published.**

   JupyterHub-plus-KubeSpawner, when used in an environment with per-user namespaces, requires a terrifying amount of privilege.  We propose moving the actual creation-of-arbitrary-objects-in-a-K8s-cluster piece of JupyterHub's job to its own service, which can be substantially simpler and present a much reduced attack surface.  Conceptually the proposed spawner service would look much like Moneypenny (and indeed Moneypenny's functionality could probably be rolled into it).  The same spawner could also be used to create Dask objects in the user's namespace.

In addition to the service itself, it would be necessary to write adaptor libraries for (at least) JupyterHub and Dask.  For JupyterHub this would implement a Spawner interface, but the mechanism by which the Spawner did its thing would simply be making web calls to the Spawning Service.  A similar approach could be used for Dask, to replace the Kubernetes calls to create Dask pods and services with HTTP calls to the Spawning Service.

.. Add content here.

Service architecture
====================

.. diagrams:: hub.py

.. diagrams:: dask.py

.. Do not include the document title (it's automatically added from metadata.yaml).

.. .. rubric:: References

.. Make in-text citations with: :cite:`bibkey`.

.. .. bibliography:: local.bib lsstbib/books.bib lsstbib/lsst.bib lsstbib/lsst-dm.bib lsstbib/refs.bib lsstbib/refs_ads.bib
..    :style: lsst_aa
