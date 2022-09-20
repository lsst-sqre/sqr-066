:tocdepth: 1

Abstract
========

The Notebook Aspect of the Rubin Science Platform is built on top of JupyterHub_ and JupyterLab_, running inside Kubernetes.
The Science Platform must spawn Kubernetes pods and associated resources for users on request, maintain internal state for which pods are running so that the user is directed to their existing pod if present, and shut down pods when requested, either by the user or the system.
Spawning a pod may also require privileged setup actions, such as creating a home directory for the user.

.. _JupyterHub: https://jupyter.org/hub
.. _JupyterLab: https://jupyter.org/

JupyterHub provides a Kubernetes spawner that can be used for this purpose, but it requires granting extensive Kubernetes permissions directly to JupyterHub, a complex piece of software that is directly exposed to user requests.
This tech note proposes an alternative design using a JupyterHub spawner that delegates all of its work to a RESTful web service, and an implementation of that web service that performs the spawning along with associated Science Platform business logic such as user home directory creation and construction of the spawner options form.
This same approach can be used for a custom Dask_ spawner for parallel computation within the Science Platform.

.. _Dask: https://www.dask.org/

Background
==========

See DMTN-164_ for a complete description of the v2 architecture of the Notebook Aspect of the Rubin Science Platform.
This tech note only touches on aspects relevant to pod spawning.

.. _DMTN-164: https://dmtn-164.lsst.io/

The v2 architecture uses `JupyterHub Kubernetes Spawner`_ to spawn user pods for notebooks, with significant local customizations via hooks defined in the nublado2_ package.
Those hooks create additional Kubernetes resources used by the pod: multiple ``ConfigMap`` resources with configuration, :file:`/etc/passwd` and related files for username and group mappings inside the pod; and Kubernetes secrets (via vault-secrets-operator_).
Each user pod and its associated resources are created in a per-user namespace.
In addition, to support a proof-of-concept Dask integration, the initial implementation creates a ``ServiceAccount``, ``Role``, and ``RoleBinding`` for the user to allow them to spawn pods in their namespace.

.. _JupyterHub Kubernetes Spawner: https://jupyterhub-kubespawner.readthedocs.io/en/latest/
.. _nublado2: https://github.com/lsst-sqre/nublado2
.. _vault-secrets-operator: https://github.com/ricoberger/vault-secrets-operator

The Kubernetes installation of JupyterHub in this architecture uses Helm and the `Zero to JupyterHub`_ Helm chart.
Configuration for the spawner is injected through the Helm chart and a custom ``ConfigMap`` resource.
The list of resources to create in each user namespace when spawning is specified in the Helm configuration as Jinja_ templates, which are processed during spawn by the custom spawn hooks.

.. _Zero to JupyterHub: https://zero-to-jupyterhub.readthedocs.io/en/latest/
.. _Jinja: https://jinja.palletsprojects.com/en/latest/

This system is supported by two ancillary web services.
moneypenny_ is called before each pod spawn to provision the user if necessary.
Currently, the only thing that it does is create user home directories in deployments where this must be done before the first spawn.
This design is described in SQR-052_.

.. _moneypenny: https://github.com/lsst-sqre/moneypenny/
.. _SQR-052: https://sqr-052.lsst.io/

Second, since the images used for user notebook pods are quite large, we prepull those images to each node in the Kubernetes cluster.
This is done by cachemachine_.
The cachemachine web service is also responsible for determining the current recommended lab image and decoding the available image tags into a human-readable form for display in the spawner menu.

.. _cachemachine: https://github.com/lsst-sqre/cachemachine/

Problems
--------

We've encountered several problems with this approach over the past year.

- The current Dask proof-of-concept grants the user creation permissions in Kubernetes to spawn pods.
  Since pod security policies are not implemented on Science Platform deployments, this allows users to spawn privileged pods and mount arbitrary file systems, thus bypassing security permissions in the cluster and potentially compromising the security of the cluster itself via a privileged pod.

- Allowing JupyterHub itself to spawn the pods requires granting extensive Kubernetes permissions to JupyterHub, including full access to create and execute arbitrary code inside pods, full access to secrets, and full access to roles and role bindings.
  Worse, because each user is isolated in their own namespace, JupyterHub has to have those permissions globally for the entire Kubernetes cluster.
  While JupyterHub itself is a privileged component of the Science Platform, it's a complex, user-facing component and good privilege isolation practices argue against granting it that broad of permissions.
  A compromise of JupyterHub currently means a complete compromise of the Kubernetes cluster and all of its secrets.

- Creation of the additional Kubernetes resources via hooks is complex and has been error-prone.
  We've had multiple problems with race conditions where not all resources have been fully created (particularly the ``Secret`` corresponding to a ``VaultSecret`` and the token for the user's ``ServiceAccount``) before the pod is spawned, resulting in confusing error messages and sometimes spawn failures.
  The current approach also requires configuring the full list of resources to create in the values file for the Notebook Aspect service, which is awkward to maintain and override for different environments.

- JupyterHub sometimes does a poor job of tracking the state of user pods and has had intermittent problems starting them and shutting them down cleanly.
  These are hard to debug or remedy because the code is running inside hooks inside the kubespawner add-on in the complex JupyterHub application.

- The interrelated roles of the nublado2, moneypenny, and cachemachine Kubernetes services in the spawning process is somewhat muddled and complex, and has led to problems debugging service issues.
  nublado2 problems sometimes turn out to be cachemachine problems or moneypenny problems but it's not obvious that this is the case from the symptoms, error messages, or logs.

- We have other cases where we would like to spawn pods in the Kubernetes environment with similar mounts and storage configuration to Notebook Aspect pods but without JuptyerHub integration, such as for privileged administrator actions in the cluster.
  Currently, we have to spawn these pods manually because the JupyterHub spawning mechanism cannot be used independently of JupyterHub.

Proposed design
===============

The proposed replacement design moves pod spawning into a separate web service and replaces the spawner implementation in JupyterHub with a thin client for that web service.
The spawner service would subsume cachemachine and moneypenny and thus take over responsibility for prepulling images, constructing the spawner options form based on knowledge of the available images, and performing any provisioning required for the user before starting their lab.

The spawner service API would also be available for non-JupyterHub spawning, including Dask.
This would replace the Kubernetes spawning code in `Dask Kubernetes`_.

.. _Dask Kubernetes: https://kubernetes.dask.org/en/latest/index.html

As a result of those changes, all Kubernetes cluster permissions can be removed from both JupyterHub and the spawned user pods.
Instead, both JupyterHub and user pods (via Dask) would make requests to the spawner service, authenticated with either JupyterHub's bot user credentials or the user's notebook token.
That authentication would be done via the normal Science Platform authentication and authorization system (see DMTN-234_).
The spawner service can then impose any necessary restrictions, checks, and verification required to ensure that only safe and expected spawning operations are allowed.

.. _DMTN-234: https://dmtn-234.lsst.io/

Only the spawner service itself will have permissions on the Kubernetes cluster.
It will be smaller, simpler code audited by Rubin Observatory with a very limited API exposed to users.

Inside JupyterHub, we would replace the ``KubeSpawner`` class with a ``RESTSpawner`` class whose implementation of all of the spawner methods is to make a web service call to the spawner service.
We can use the user's own credentials to authenticate the spawn call to the spawner service, which ensures that a compromised JupyterHub cannot spawn pods as arbitrary users.
Other calls can be authenticated with JupyterHub's own token, since they may not be associated with a user request.

The spawner service will know which user it is spawning a pod for, and will have access to the user's metadata, so it can set quotas, limit images, set environment variables, and take other actions based on the user and Science Platform business logic without having to embed all of that logic into JupyterHub hooks.

Here is that architecture in diagram form.

.. figure:: /_static/architecture.png
   :name: Notebook Aspect spawner architecture

   High-level structure of the JupyterHub architecture using an external spawner.
   This diagram is somewhat simplified for clarity.
   The lab may also talk to the spawner to spawn Dask pods, JupyterHub and the lab talk over the internal JupyterHub protocol, and both JupyterHub and the lab talk to the spawner via the ingress rather than directly.

Here is a sequence diagram of the new spawning process.

.. figure:: /_static/spawning.svg
   :name: Lab spawning sequence

   Sequence of operations for lab spawning.
   Authentication and authorization steps have been omitted for clarity.

The Dask spawning process will look very similar, except that the request will be coming from the user's lab and the Dask pods will be considered child pods of the lab pod.
A shutdown request for the lab pod will also shut down all of the Dask pods.

Spawner REST API
================

Initial routes for the spawner API.
This design makes the explicit assumption that a given user may only have one lab running at a time.
Supporting multiple labs for the same user (something that is supported by JupyterHub but not by the current design of the Rubin Science Platform) would require a redesign of the API.

This API will be protected by the regular authentication mechanism for the Rubin Science Platform, described in DMTN-224_.
It will use multiple ingresses to set different authentication requirements for different routes.
The ``POST /spawner/v1/labs/<username>/spawn`` route will request a delegated notebook token, which it will provide to the spawned pod so that the user has authentication credentials inside their lab.

.. _DMTN-224: https://dmtn-224.lsst.io/

The ``admin:notebook`` scope is a new scope granted only to the JupyterHub pod itself and (optionally) Science Platform administrators.
It controls access to APIs that only JupyterHub needs to use.

If Science Platform administrators need to test pod spawning or see the event stream directly, they should use user impersonation (creating a token with the identity of the user that they're debugging).

``GET /spawner/v1/labs``
    Returns a list of all users with running labs.
    Example:

    .. code-block:: json

       ["adam", "rra"]

    Credential scopes required: ``admin:notebook``

``GET /spawner/v1/labs/<username>``
    Returns status of the lab pod for the given user, or 404 if that user has no running or starting lab.
    Example:

    .. code-block:: json

       {
           "username": "rra",
           "status": "starting",
           "pod": "missing",
           "options": {
               "debug": false,
               "image": "lsstsqre/sciplat-lab:w_2022_37",
               "reset_user_env": false,
               "size": "large"
           },
           "env": {
               "JUPYTERHUB_API_URL": "http://hub.nublado2:8081/nb/hub/api"
           },
           "uid": 4266950,
           "gid": 4266950,
           "groups": [
               {
                   "name": "lsst-data-management",
                   "id": 170034
               },
               {
                   "name": "rra",
                   "id": 4266950
               }
           ],
           "quotas": {
               "limits": {
                   "cpu": 4,
                   "memory": 12884901888
               },
               "requests": {
                   "cpu": 4,
                   "memory": 1073741824
               }
           }
       }

    The response contains a mix of information provided at lab creation (options and env), information derived from the user's identity used to create the lab (UID, GID, group membership), and information derived from other settings (the quotas, which are based primarily on the chosen size).
    ``status`` is one of ``starting``, ``running``, ``terminating``, or ``failed``.
    ``pod`` is one of ``present`` or ``missing`` and indicates the spawner's understanding of whether the corresponding Kubernetes pod exists.
    (This is relevant primarily for a lab in ``failed`` status.)

    If spawning a lab for that user was attempted but failed, the record of that failure is retained with a ``failed`` status and its events (see the ``GET /spawner/v1/labs/<username>/events`` route description) will continue to be available until lab creation is attempted again for that user or the spawner service restarts or garbage-collects old information.

    Credential scopes required: ``admin:jupyterlab``

``POST /spawner/v1/labs/<username>/spawn``
    Create a new lab pod for a given user.
    Returns status 303 with a ``Location`` header pointing to ``/spawner/v1/labs/<username>`` if creation of the lab pod has been successfully queued.

    This uses a separate route instead of a ``PUT`` verb on the ``/spawner/v1/labs/<username>`` route because it needs separate Gafaelfawr configuration.
    (Specifically, it needs to request a delegated notebook token so that it can be provided to the spawned lab.)

    This route returns as soon as the creation is queued.
    To monitor the status of the pod creation, use ``GET /spawner/v1/labs/<username>/events``.

    The body of the ``POST`` request is a specification for the lab.
    Example:

    .. code-block:: json

       {
           "options": {
               "debug": true,
               "image": "sciplat/sciplat-lab:w_2022_37",
               "reset_user_env": true,
               "size": "large"
           },
           "env": {
               "JUPYTERHUB_API_URL": "http://hub.nublado2:8081/nb/hub/api"
           }
       }

    The keys of the ``options`` dictionary should be the parameters submitted by a ``POST`` of the form returned by ``GET /spawner/v1/spawn-form/<username>``.

    If a lab for the user already exists, this request will fail with a 409 status code.
    The configuration of the existing lab cannot be modified with a ``POST`` request.
    It must be deleted and recreated.
    If a lab exists in the ``failed`` status, a new lab can be created for that user, and the old failure information from the previous lab will be discarded.
    When creating a new lab when one exists in ``failed`` status, if ``pod`` is ``present``, the spawner will attempt again to remove the old pod first.

    Credential scopes required: ``exec:notebook``
    JupyterHub cannot create labs for arbitrary users without using a delegated token from that user.

``GET /spawner/v1/labs/<username>/events``
    Returns the spawning events for a lab, suitable for display in the JupyterHub spawner status page.
    This is a stream of `server-sent events`_.

    .. _server-sent events: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events

    If the lab spawning has completed (either because the lab is in ``running`` status or has failed), the server-sent events stream will be closed.
    Otherwise, it will stay open until the spawn or delete operation is complete.
    This can therefore be used by the JupyterHub spawner API to wait for completion of the spawn operation.

    The following event types are defined:

    ``complete``
        Indicates that the lab has successfully spawned.
        The ``data`` field must be present but contains no useful information.

    ``error``
        An error or warning in the spawning process.
        The ``data`` field will be a human-readable message.

    ``failed``
        Indicates that the lab has failed to spawn.
        The ``data`` field must be present but contains no useful information.

    ``info``
        An informational message or a completion of a stage in the spawning process.
        The ``data`` field will be a human-readable message.

    ``progress``
        An update to the progress bar.
        The ``data`` field will be the estimated completion percentage.

    Calling ``POST /spawner/v1/labs/<username>/spawn`` or ``DELETE /spawner/v1/labs/<username>`` clears the previous saved event stream and starts a new event stream for that operation.
    Only one operation can be in progress at a time, and the event stream only represents the current operation.

    Credential scopes required: ``exec:notebook``

``DELETE /spawner/v1/labs/<username>``
    Stop a running pod.
    Returns 202 on successful acceptance of the request and 404 if no lab for this username is currently running.

    This puts the lab in ``terminating`` state and starts the process of stopping it and deleting its associated resources.
    The progress of that termination can be retrieved from ``GET /spawner/v1/labs/<username>/events``.

    If termination is successful, the resource is removed.
    If termination is unsuccessful, the lab is put into a ``failed`` state and retained for error reporting.

    Credential scopes required: ``admin:notebook``
    JupyterHub can delete labs without having the user's credentials available, since this may be required to clean up state after an unclean restart of the service.

``GET /spawner/v1/spawn-form/<username>``
    Get the spawner form for a particular user.
    The form may be customized for the user; for example, some images or lab sizes may only be available to certain users.

    The result is ``text/html`` suitable for inclusion in the lab spawning page of JupyterHub.
    It will define a form whose elements correspond to the keys of the ``options`` parameter to the ``POST /spawner/v1/labs/<username>/spawn`` call used to create a new lab.
    Each parameter should be single-valued.

    Credential scopes required: ``exec:notebook``
    JupyterHub cannot retrieve the spawn form for arbitrary users, only for the user for whom it has a delegated token, since the identity of the token may be used to determine what options are available.

``GET /spawner/v1/user-status``
    Get the pod status for the authenticating user.
    If the user does not have a pod, returns 404.

    This is identical to the ``GET /spawner/v1/labs/<username>`` route except that it only requires the ``exec:notebook`` scope, so users can use it, and the username is implicitly the calling user.
    It allows a user to obtain status information for their own pod and may be used under the hood by the top-level UI for the Science Platform.
    (For example, the UI may change the Notebook Aspect portion of the page to indicate the user already has a running lab they can reconnect to, rather than indicating that the user can spawn a new lab.)

    Credential scopes required: ``exec:notebook``

The API to spawn Dask pods is not yet defined in detail, but will look very similar to the above API, except that it will use a resource nested under the lab.
For example, ``/spawner/v1/labs/<username>/dask-pool/<name>``.

JupyterHub spawner class
========================

As discussed above, using a separate spawner service requires replacing Kubespawner with a new spawner implementation.
Some of the required details will not be obvious until we try to implement it, but here is a sketch of how the required spawner methods can be implemented.
This is based on the `JupyterHub spawner documentation <https://jupyterhub.readthedocs.io/en/stable/reference/spawners.html>`__ (which unfortunately is woefully incomplete at the time of this writing).

The spawner implementation will assume that the ``token`` element of the authentication state in JupyterHub contains the delegated authentication credentials for the user, and use them to authenticate to the spawner.

``options_form``
    Calls ``GET /spawner/v1/spawn-form/<username>``, authenticated as the user, and returns the resulting HTML.

``options_from_form``
    Converts the parameters submitted to the spawner form into a form suitable to pass to the spawner service.
    The input form submission will be a map of keys to lists of strings.
    Each list must contain only one string, and then the strings ``true`` and ``false`` will be converted to their boolean values.
    This will form the content of the ``options`` parameter to the ``POST`` call to start a lab.

``start``
    Calls ``POST /spawner/v1/labs/<username>/spawn``, and then waits for the lab to finish starting.
    The waiting is done via ``GET /spawner/v1/labs/<username>/events`` and waiting for a ``complete`` or ``failed`` event.

    The ``options`` parameter in the ``POST`` body is set to the spawner form data transformed by ``options_from_form``.
    The ``env`` parameter in the ``POST`` body is set to the return value of the ``get_env`` method (which is not overridden by this spawner implementation).

    Calling ``start`` clears the events for that user.
    Then, while waiting, the ``start`` coroutine updates an internal data structure holding a list of events for that user.
    Each event should be an (undocumented) JupyterHub spawner progress event.

    .. code-block:: json

       {
           "progress": 80,
           "message": "text",
           "html_message": "html_text"
       }

    ``progress`` is a number out of 100 indicating percent completion.
    ``html_message`` is optional and is used when rendering the message on a web page.

    This doesn't exactly match the event stream provided by the spawner.
    To convert, keep the current progress state and update it when a ``progress`` event is received, without emiting a new event.
    Then emit an event with the last-seen progress for any ``info`` or ``error`` events.
    Set internal state indicating that the operation is complete and then emit completion and failure events (with a progress of 100) upon seeing a ``complete`` or ``failure`` event.

    These events are used in the implementation of the ``progress`` method described below.
    The event data structure should be protected by a per-user ``asyncio.Condition`` lock.
    The ``start`` method will acquire the lock on each event, update state as needed, and then if an ``info``, ``error``, ``complete``, or ``failure`` event was received, call ``notify_all`` on the condition to awaken any threads of execution waiting on the condition in the ``progress`` method.

``stop``
    Calls ``DELETE /spawner/v1/labs/<username>`` to stop the user's lab and wait for it to complete.
    As with ``start``, the waiting is done via ``GET /spawner/v1/labs/<username>/events`` and waiting for a ``complete`` or ``failed`` event.

    Calling ``stop`` clears the events for that user.
    Then, while waiting, the ``stop`` coroutine updates an internal data structure holding a list of events for that user, in exactly the same way as ``start``.

``poll``
    Calls ``GET /spawner/v1/labs/<username>`` to see if the user has a running lab.
    Returns ``None`` if the lab is in ``starting``, ``running``, or ``terminating`` state, and ``0`` if it is in ``failed`` state or does not exist.

``progress``
    Yields (as an async generator) the list of progress events accumulated by the previous ``start`` or ``stop`` method call.
    Returns once internal state has marked the operation complete.

    This is implemented by taking a lock on the event list for this user, returning all of the accumulated events so far, ending the loop if the operation is complete, and if not, waiting on the per-user ``asyncio.Condition`` lock.
    All ``progress`` calls for that user will then be woken up by ``start`` or ``stop`` when there's a new message, and can yield that message and then wait again if the operation is still not complete.

``get_state``, ``load_state``, ``clear_state``
    This spawner implementation doesn't truly require any state, but reportedly one has to store at least one key or JupyterHub thinks the lab doesn't exist.
    ``get_state`` will therefore record the event information used by ``progress`` (events, progress amount, and completion flag).
    ``load_state`` will restore it, and ``clear_state`` will clear it.

The ``mem_limit``, ``mem_guarantee``, ``cpu_limit``, and ``cpu_guarantee`` configurables in the spawner class are ignored.
Quotas are set as appropriate in the spawner service based on metadata about the user and the chosen options on the spawner form.

Similarly, the ``cmd`` and ``args`` configuration parameters to the spawner are ignored.
The spawner service will always spawn the JupyterLab single-user server.

Pod configuration
=================

Each spawned user lab pod, and any Dask pods for that lab pod, will live in a per-user namespace.
The namespace will be called ``nublado-<username>``.

When shutting down a lab, first the pod will be stopped and then the namespace will be deleted, cleaning up all other resources.

Resources in the namespace will be prefixed with ``nb-<username>-``.
This allows for easier sorting in management displays such as Argo CD.

UID and GIDs
------------

The lab pod will always be spawned as the user's UID and primary GID, as taken from the user identity information associated with their token.
If privileged actions are needed, they will be done via a separate sidecar container.
See :ref:`User provisioning <provisioning>` for more information.

The supplemental groups of the lab pod will be set to the list of all the GIDs of the user's group, except for their primary GID.
Group memberships in groups that do not have GIDs are ignored for the purposes of constructing the supplemental group list.

Environment
-----------

The environment of the spawned pod is a combination of three sources of settings, here listed in the order in which they override each other.

#. The ``env`` parameter to the ``POST /spawner/v1/labs/<username>/spawn`` call used to spawn the lab.
   This in turn comes straight from JupyterHub.
#. Settings added directly by the spawner.
   ``MEM_LIMIT``, ``MEM_GUARANTEE``, ``CPU_LIMIT``, and ``CPU_GUARANTEE`` are set to match the quotas that it calculates based on the user identity and the requested image size.
   (This matches the default spawner behavior.)
   ``IMAGE_DIGEST`` and ``IMAGE_DESCRIPTION`` will be set to the digest and human-readable description of the chosen image.
   Other variables may be set based on the options provided via the ``POST`` that spawned the lab in order to control the behavior of the lab startup scripts.
#. Settings added via the spawner configuration.
   The Helm chart for the spawner service will allow injection of environment variables that should always be set in a given Science Platform deployment.
   This includes, for example, deployment-specific URLs used for service discovery or environment variables used to configure access to remote resources.

The pod environment will be stored in a ``ConfigMap`` named ``nb-<username>-env`` and used as the default source for environment variables in the pod.
Since the spawner is under control of both the ``Pod`` object and the ``ConfigMap`` object, all environment variables not from secrets will be stored in the ``ConfigMap`` instead of set directly in the ``Pod`` object.
This makes it easier for humans to understand the configuration.

User and group mappings
-----------------------

The Notebook Aspect of the Science Platform uses a POSIX file system as its primary data store.
That means it uses numeric UIDs and GIDs for access control and to record ownership and creation information.

To provided the expected POSIX file system view from the Notebook Aspect, mappings of those UIDs and GIDs to human-readable usernames and group names must be provided.
The spawner service does this by generating ``/etc/passwd`` and ``/etc/group`` files and mounting them into the lab container over top of the files provided by the container image.

The base ``/etc/passwd`` and ``/etc/group`` files are whatever minimal files are required to make the container work and provide reasonable human-readable usernames and groups for files present in the container.

``/etc/passwd`` as mounted in the container has one added entry for the user.
Their name, UID, and primary GID are taken from the user identity information associated with their token.
The home directory is always ``/home/<username>`` and the shell is always ``/bin/bash``.

``/etc/group`` as mounted in the container has an entry for each group in the user's group membership that has an associated GID.
Groups without GIDs cannot be meaningfully represented in the ``/etc/group`` structure and are ignored.
The user is added as a supplementary member of the group unless the GID of the group matches the user's primary GID.

No ``/etc/shadow`` or ``/etc/gshadow`` files are mounted in the pod.
The pod is always executed as the intended user and PAM should not be used or needed, so nothing should need or be able to read those files.

The ``/etc/passwd`` and ``/etc/group`` files will be stored under ``passwd`` and ``group`` keys in a ``ConfigMap`` named ``nb-<username>-nss`` (from Name Service Switch, the name of the Linux subsystem that provides this type of user and group information), and mounted via the ``Pod`` specification.

Mounts
------

The necessary volume mounts for a lab pod will be specified in the Helm configuration of the spawner service.
At the least, this will include a mount definition for ``/home``, where user home directories must be located inside the pod.

Secrets
-------

Each lab pod will have an associated ``Secret`` object named ``nb-<username>`` containing any required secrets.

It will have at least one key, ``token``, which holds the notebook token for the user that is mounted into the pod and used to make API calls from the Notebook Aspect.
This token is obtained from the ``POST`` request that spawns the lab, via the ``X-Auth-Request-Token`` header added by Gafaelfawr.
That route in the spawner API will request a delegated notebook token.

Additional secrets may be added via the Helm configuration of the spawner service.
Each configured secret should be a reference to another ``Secret`` in the spawner service namespace and a key in that ``Secret`` object that should be added to the ``Secret`` object for each spawned pod.
The secrets will be copied into the created ``Secret`` object during pod spawn.

One of those secrets may be tagged as a pull secret, in which case the required configuration to use it as a pull secret will also be added to the ``Pod`` specification.

Argo CD support
---------------

All created resources will have the following annotations added:

.. code-block:: yaml

   argocd.argoproj.io/compare-options: "IgnoreExtraneous"
   argocd.argoproj.io/sync-options: "Prune=false"

They will also have the following labels added:

.. code-block:: yaml

   argocd.argoproj.io/instance: "nublado-users"

This will cause all user resources created by the spawner to appear under the Argo CD application ``nublado-users``, which allows them to be explored and manipulated via the Argo CD UI even though they are not managed directly by Argo CD.

The drawback is that the ``nublado-users`` application will always display as "Progressing" because it contains unmanaged pods that are still running.

.. _provisioning:

User provisioning
-----------------

The spawner service will also take over from moneypenny the responsibility for doing initial user provisioning.
Instead of launching a separate pod and waiting for it to complete before starting the lab pod, user provisioning, if needed, will be done via an init container run as part of the same ``Pod`` object as the lab container.

If the spawner service is configured with a user provisioning container in its Helm chart, and it has not previously spawned a pod for a given user, it will add an init container to the ``Pod`` specification.
The specification for that container will be taken from its Helm configuration.
Once that pod has successfully spawned (but not if it fails to spawn), the spawner will mark that user as having been provisioned and will not add an init container for them in the future.

Init containers may still be run multiple times for a given user, since the spawner service will lose its records of which users have already been initialized when it is restarted.
Therefore, any configured init container must be idempotent and safe to run repeatedly for the same user.

Decommissioning containers (for when a user is deleted) are not part of this specification and will not be supported initially.
We may add them later if we discover a need.

Prepulling
==========

The spawner service will also handle prepulling selected images onto all nodes in the cluster so that spawning labs for the Notebook Aspect will be faster.
It does this by using the Kubernetes API to ask each node what images it has cached, and then spawning a ``DaemonSet`` as needed to cache any images that are missing.

Configuration
-------------

Prepulling is configured via the Helm chart for the spawner service.
The prepuller configuration also serves as configuration for the image selection portion of the lab spawner form, since it controls what images are listed for selection outside of the dropdown menu of all available tags.

An example of a prepuller configuration:

.. code-block:: yaml

   prepull:
     - name: jupyter
       type: rubin
       registry: registry.hub.docker.com
       docker:
         repository: lsstsqre/sciplat-lab
       recommendedTag: recommended
       numReleases: 1
       numWeeklies: 2
       numDailies: 3

The ``name`` field says that ``jupyter`` is the name of this prepuller configuration.
This is the name used in the ``/spawner/v1/prepulls/<name>`` routes in the REST API to inspect the prepulling status.

This configuration pulls a group of images from the ``lsstsqre/sciplat-lab`` Docker image repository at registry.hub.docker.com that follow the tag conventions documented in SQR-059_.
The latest release, the latest two weeklies, and the latest three dailies will be prepulled.
Whatever image has the tag ``recommended`` will appear as the first and default selected image.

.. _SQR-059: https://sqr-059.lsst.io/

Another example that uses Google Artifact Repository and explicitly pulls an image regardless of its recency:

.. code-block:: yaml

   prepull:
     - name: jupyter
       type: rubin-gar
       registry: us-central1-docker.pkg.dev
       gar:
         repository: sciplat
         image: sciplat-lab
         projectId: rubin-shared-services-71ec
         location: us-central1
       recommendedTag: recommended
       numReleases: 1
       numWeeklies: 2
       numDailies: 3
     - name: recommended
       type: simple
       images:
         - url: us-central1-docker.pkg.dev/rubin-shared-services-71ec/sciplat/sciplat-lab:w_2022_22
           name: "Weekly 2022_22"

This uses Google Artifact Registry as the source of containers instead of a Docker image repository compatible with the Docker Hub protocol.
It also has a second stanza that ensures that a specific named image is always pulled, regardless of whether it is one of the latest releases, weeklies, or dailies.

Finally, here is an example for a Telescope and Site deployment that limits available images to those that implement a specific cycle:

.. code-block:: yaml

   prepull:
     - name: jupyter
       type: rubin
       registry: ts-dockerhub.lsst.org
       docker:
         repository: sal-sciplat-lab
       recommendedTag: recommended_c0026
       numReleases: 0
       numWeeklies: 3
       numDailies: 2
       cycle: 26
       aliasTags:
         - latest
         - latest_daily
         - latest_weekly

This is very similar to the first example but adds a ``cycle`` option that limits available images to those implementing that cycle.
It also includes an ``aliasTags`` option that lists tags that should be treated as aliases of other tags, rather than possible useful images in their own right.

REST API
--------

To facilitate debugging of prepuller issues, there is a read-only REST API to see the status of a prepull configuration.
Changing the configuration requires changing the Helm chart or the generated ``ConfigMap`` object and restarting the spawner service.

All of these API calls require ``admin:notebook`` scope.

``GET /spawner/v1/prepulls``
    Returns status of the known prepull configurations.
    The response is a JSON object with two keys.

    The first key is ``configs``, which contains a list of prepull configurations.
    These is nearly identical to the configuration blocks given above, converted to JSON, but one additional field in each configuration:

    .. code-block:: json

       "images": {
           "prepulled": [
               {
                   "url": "<full image url>",
                   "name": "<human readable name>",
                   "hash": "<image hash>",
                   "nodes": ["<node>", "<node>"]
               }
           ],
           "pending": [
               {
                   "url": "<full image url>",
                   "name": "<human readable name>"
                   "hash": "<image hash>",
                   "nodes": ["<node>", "<node>"],
                   "missing": ["<node>", "<node>"]
               }
           ],
           "other": [
               {
                   "url": "<full image url>",
                   "name": "<human readable name>"
               }
           ]
       }

    ``prepulled`` lists the images that the spawner believes have been successfully prepulled to every node based on this prepull configuration.
    ``pending`` lists the ones that still need to be prepulled.
    ``other`` lists the other tags for the prepulled repository that are not being prepulled based on the configuration.

    For each image, ``nodes`` contains a list of the nodes to which that image has been prepulled, and ``missing`` contains a list of the nodes that should have that image but do not.

    The second key is ``nodes``, which contains a list of nodes.
    Each node has the following structure:

    .. code-block:: json

       {
           "name": "<name>",
           "eligible": true,
           "comment": "<why ineligible>",
           "cached": ["<image>", "<image>"]
       }

    ``eligible`` is a boolean saying whether this node is eligible for prepulling.
    If it is false, the reason for its ineligibility will be given in ``comment``; otherwise, ``comment`` will be missing.
    ``cached`` is a list of image URLs that are cached on that node.

Future work
===========

- The API and Python implementation for Dask spawning has not yet been designed.
  This will require new routes for spawning and deleting Dask pods under the route for the user's lab, and a way to get configuration information such as the user's quota of Dask pods or the CPU and memory quotas of each pod.
  All Dask pods should be automatically deleted when the user's lab is deleted.

- The spawner should also support launching privileged pods for administrative maintenance outside of the Notebook Aspect.
  This will require a new API protected by a different scope, not ``admin:notebook``, since JupyterHub should not have access to spawn such pods.

- A more detailed specification of the configuration for provisioning init containers should be added, either here or (preferrably) in operational documentation once this spawner service has been implemented.

- The routes to return information about pods and prepull configurations are likely to need more detail.
  The draft REST API specifications in this document should be moved into code and replaced with documentation generated by OpenAPI, similar to what was done for Gafaelfawr_.

.. _Gafaelfawr: https://gafaelfawr.lsst.io/
