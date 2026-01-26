
Installation
============

PyPI
----

.. code-block:: bash

   pip install genro-mail-proxy

This installs the ``mail-proxy`` CLI command.

Docker
------

Build and run:

.. code-block:: bash

   docker build -t genro-mail-proxy .
   docker run -p 8000:8000 -v mail-data:/data genro-mail-proxy

Environment variables:

- ``GMP_DB_PATH``: Database connection string. Formats:

  - ``/path/to/db.sqlite`` - SQLite file (default: ``/data/mail_service.db``)
  - ``postgresql://user:pass@host:5432/db`` - PostgreSQL

Docker Compose with PostgreSQL
------------------------------

The default ``docker-compose.yml`` includes PostgreSQL:

.. code-block:: yaml

   # docker-compose.yml
   services:
     db:
       image: postgres:16-alpine
       environment:
         POSTGRES_USER: mailproxy
         POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
         POSTGRES_DB: mailproxy
       volumes:
         - pgdata:/var/lib/postgresql/data
       healthcheck:
         test: ["CMD-SHELL", "pg_isready -U mailproxy"]
         interval: 5s
         timeout: 5s
         retries: 5

     mailservice:
       build: .
       image: genro-mail-proxy:latest
       environment:
         - GMP_DB_PATH=postgresql://mailproxy:${POSTGRES_PASSWORD:-changeme}@db:5432/mailproxy
       ports:
         - "8000:8000"
       depends_on:
         db:
           condition: service_healthy
       restart: unless-stopped

   volumes:
     pgdata:

.. code-block:: bash

   # Start with default password
   docker compose up -d

   # Or set a custom password
   POSTGRES_PASSWORD=mysecret docker compose up -d

Docker Compose with SQLite
--------------------------

For simpler deployments using SQLite:

.. code-block:: yaml

   # docker-compose-sqlite.yml
   services:
     mailservice:
       build: .
       image: genro-mail-proxy:latest
       environment:
         - GMP_DB_PATH=/data/mail_service.db
       ports:
         - "8000:8000"
       volumes:
         - maildata:/data
       restart: unless-stopped

   volumes:
     maildata:

.. code-block:: bash

   docker compose -f docker-compose-sqlite.yml up -d

Local Development
-----------------

.. code-block:: bash

   # Clone and install in development mode
   git clone https://github.com/softwell/genro-mail-proxy.git
   cd genro-mail-proxy
   pip install -e ".[dev]"

   # Run tests
   pytest

   # Start the server
   mail-proxy start myserver

Network Requirements
--------------------

For production deployment, ensure proper network connectivity:

1. **Client → mail-proxy**: HTTP/HTTPS on port 8000 (or configured ``GMP_PORT``)
2. **mail-proxy → Client**: HTTP/HTTPS for delivery reports (tenant's ``sync_path``)
3. **mail-proxy → SMTP**: Outbound TCP on port 587 (STARTTLS) or 465 (SMTPS)

See :doc:`network_requirements` for detailed firewall rules and deployment scenarios.

Kubernetes
----------

For production Kubernetes deployments, use the following manifests as a starting point.

ConfigMap and Secret
~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   # configmap.yaml
   apiVersion: v1
   kind: ConfigMap
   metadata:
     name: mail-proxy-config
   data:
     GMP_PORT: "8000"
     GMP_LOG_LEVEL: "INFO"

   ---
   # secret.yaml
   apiVersion: v1
   kind: Secret
   metadata:
     name: mail-proxy-secret
   type: Opaque
   stringData:
     GMP_API_TOKEN: "your-secret-api-token"
     # For PostgreSQL (recommended for production)
     GMP_DB_PATH: "postgresql://mailproxy:password@postgres-service:5432/mailproxy"

Deployment
~~~~~~~~~~

.. code-block:: yaml

   # deployment.yaml
   apiVersion: apps/v1
   kind: Deployment
   metadata:
     name: mail-proxy
     labels:
       app: mail-proxy
   spec:
     replicas: 1  # Use 1 replica with SQLite, multiple with PostgreSQL
     selector:
       matchLabels:
         app: mail-proxy
     template:
       metadata:
         labels:
           app: mail-proxy
         annotations:
           prometheus.io/scrape: "true"
           prometheus.io/port: "8000"
           prometheus.io/path: "/metrics"
       spec:
         containers:
         - name: mail-proxy
           image: genro-mail-proxy:latest
           ports:
           - containerPort: 8000
             name: http
           envFrom:
           - configMapRef:
               name: mail-proxy-config
           - secretRef:
               name: mail-proxy-secret
           resources:
             requests:
               memory: "128Mi"
               cpu: "100m"
             limits:
               memory: "512Mi"
               cpu: "500m"
           livenessProbe:
             httpGet:
               path: /health
               port: 8000
             initialDelaySeconds: 10
             periodSeconds: 30
           readinessProbe:
             httpGet:
               path: /health
               port: 8000
             initialDelaySeconds: 5
             periodSeconds: 10
           # For SQLite: mount a PVC
           # volumeMounts:
           # - name: data
           #   mountPath: /data
         # volumes:
         # - name: data
         #   persistentVolumeClaim:
         #     claimName: mail-proxy-data

Service
~~~~~~~

.. code-block:: yaml

   # service.yaml
   apiVersion: v1
   kind: Service
   metadata:
     name: mail-proxy
     labels:
       app: mail-proxy
   spec:
     type: ClusterIP
     ports:
     - port: 8000
       targetPort: 8000
       protocol: TCP
       name: http
     selector:
       app: mail-proxy

Ingress (optional)
~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   # ingress.yaml
   apiVersion: networking.k8s.io/v1
   kind: Ingress
   metadata:
     name: mail-proxy
     annotations:
       nginx.ingress.kubernetes.io/ssl-redirect: "true"
   spec:
     ingressClassName: nginx
     tls:
     - hosts:
       - mail-proxy.example.com
       secretName: mail-proxy-tls
     rules:
     - host: mail-proxy.example.com
       http:
         paths:
         - path: /
           pathType: Prefix
           backend:
             service:
               name: mail-proxy
               port:
                 number: 8000

PersistentVolumeClaim (SQLite only)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If using SQLite instead of PostgreSQL:

.. code-block:: yaml

   # pvc.yaml
   apiVersion: v1
   kind: PersistentVolumeClaim
   metadata:
     name: mail-proxy-data
   spec:
     accessModes:
     - ReadWriteOnce
     resources:
       requests:
         storage: 1Gi

.. warning::

   SQLite does not support concurrent writes from multiple pods.
   Use ``replicas: 1`` with SQLite, or use PostgreSQL for multi-replica deployments.

PostgreSQL with Helm (recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For production, deploy PostgreSQL using Bitnami Helm chart:

.. code-block:: bash

   helm repo add bitnami https://charts.bitnami.com/bitnami
   helm install postgres bitnami/postgresql \
     --set auth.username=mailproxy \
     --set auth.password=changeme \
     --set auth.database=mailproxy \
     --set primary.persistence.size=10Gi

Then update the secret:

.. code-block:: yaml

   GMP_DB_PATH: "postgresql://mailproxy:changeme@postgres-postgresql:5432/mailproxy"

Horizontal Pod Autoscaler (PostgreSQL only)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

With PostgreSQL, you can scale horizontally:

.. code-block:: yaml

   # hpa.yaml
   apiVersion: autoscaling/v2
   kind: HorizontalPodAutoscaler
   metadata:
     name: mail-proxy
   spec:
     scaleTargetRef:
       apiVersion: apps/v1
       kind: Deployment
       name: mail-proxy
     minReplicas: 2
     maxReplicas: 5
     metrics:
     - type: Resource
       resource:
         name: cpu
         target:
           type: Utilization
           averageUtilization: 70

Apply all manifests
~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   kubectl apply -f configmap.yaml
   kubectl apply -f secret.yaml
   kubectl apply -f deployment.yaml
   kubectl apply -f service.yaml
   kubectl apply -f ingress.yaml  # optional

   # Verify
   kubectl get pods -l app=mail-proxy
   kubectl logs -l app=mail-proxy
