
Python Modules
==============

This document provides API reference for the genro-mail-proxy Python modules.

The package is organized into four main namespaces:

- ``core.mail_proxy`` - Core functionality (Apache 2.0)
- ``enterprise.mail_proxy`` - Enterprise features (BSL 1.1)
- ``sql`` - Database abstraction layer
- ``storage`` - Storage node management
- ``tools`` - Shared utilities

Core Package (Apache 2.0)
-------------------------

Main Components
~~~~~~~~~~~~~~~

.. automodule:: core.mail_proxy
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.proxy
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.proxy_base
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.proxy_config
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.server
   :members:
   :undoc-members:
   :show-inheritance:

Interface Layer
~~~~~~~~~~~~~~~

.. automodule:: core.mail_proxy.interface.api_base
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.interface.cli_base
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.interface.cli_commands
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.interface.endpoint_base
   :members:
   :undoc-members:
   :show-inheritance:

Entities
~~~~~~~~

Each entity follows the table + endpoint pattern for database persistence
and API exposure.

**Account Entity**

.. automodule:: core.mail_proxy.entities.account.table
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.entities.account.endpoint
   :members:
   :undoc-members:
   :show-inheritance:

**Tenant Entity**

.. automodule:: core.mail_proxy.entities.tenant.table
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.entities.tenant.endpoint
   :members:
   :undoc-members:
   :show-inheritance:

**Message Entity**

.. automodule:: core.mail_proxy.entities.message.table
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.entities.message.endpoint
   :members:
   :undoc-members:
   :show-inheritance:

**Message Event Entity**

.. automodule:: core.mail_proxy.entities.message_event.table
   :members:
   :undoc-members:
   :show-inheritance:

**Instance Entity**

.. automodule:: core.mail_proxy.entities.instance.table
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.entities.instance.endpoint
   :members:
   :undoc-members:
   :show-inheritance:

**Command Log Entity**

.. automodule:: core.mail_proxy.entities.command_log.table
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.entities.command_log.endpoint
   :members:
   :undoc-members:
   :show-inheritance:

**Storage Entity**

.. automodule:: core.mail_proxy.entities.storage.table
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.entities.storage.endpoint
   :members:
   :undoc-members:
   :show-inheritance:

SMTP Components
~~~~~~~~~~~~~~~

.. automodule:: core.mail_proxy.smtp.sender
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.smtp.pool
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.smtp.attachments
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.smtp.cache
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.smtp.retry
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: core.mail_proxy.smtp.rate_limiter
   :members:
   :undoc-members:
   :show-inheritance:

Reporting
~~~~~~~~~

.. automodule:: core.mail_proxy.reporting.client_reporter
   :members:
   :undoc-members:
   :show-inheritance:


Enterprise Package (BSL 1.1)
----------------------------

Enterprise features require a commercial license for production use.

Proxy Extension
~~~~~~~~~~~~~~~

.. automodule:: enterprise.mail_proxy
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: enterprise.mail_proxy.proxy_ee
   :members:
   :undoc-members:
   :show-inheritance:

Bounce Detection
~~~~~~~~~~~~~~~~

.. automodule:: enterprise.mail_proxy.bounce.parser
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: enterprise.mail_proxy.bounce.receiver
   :members:
   :undoc-members:
   :show-inheritance:

PEC Support
~~~~~~~~~~~

.. automodule:: enterprise.mail_proxy.pec.parser
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: enterprise.mail_proxy.pec.receiver
   :members:
   :undoc-members:
   :show-inheritance:

IMAP Client
~~~~~~~~~~~

.. automodule:: enterprise.mail_proxy.imap.client
   :members:
   :undoc-members:
   :show-inheritance:

Large File Storage
~~~~~~~~~~~~~~~~~~

.. automodule:: enterprise.mail_proxy.attachments.large_file_storage
   :members:
   :undoc-members:
   :show-inheritance:

Enterprise Entities
~~~~~~~~~~~~~~~~~~~

**Account EE (PEC Support)**

.. automodule:: enterprise.mail_proxy.entities.account.table_ee
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: enterprise.mail_proxy.entities.account.endpoint_ee
   :members:
   :undoc-members:
   :show-inheritance:

**Tenant EE (API Keys)**

.. automodule:: enterprise.mail_proxy.entities.tenant.table_ee
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: enterprise.mail_proxy.entities.tenant.endpoint_ee
   :members:
   :undoc-members:
   :show-inheritance:

**Instance EE (Bounce Config)**

.. automodule:: enterprise.mail_proxy.entities.instance.table_ee
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: enterprise.mail_proxy.entities.instance.endpoint_ee
   :members:
   :undoc-members:
   :show-inheritance:

**Message EE (PEC Events)**

.. automodule:: enterprise.mail_proxy.entities.message.table_ee
   :members:
   :undoc-members:
   :show-inheritance:

**Storage EE (Cloud Backends)**

.. automodule:: enterprise.mail_proxy.storage.node_ee
   :members:
   :undoc-members:
   :show-inheritance:


SQL Package
-----------

Database abstraction layer supporting SQLite and PostgreSQL.

.. automodule:: sql.sqldb
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: sql.table
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: sql.column
   :members:
   :undoc-members:
   :show-inheritance:

Database Adapters
~~~~~~~~~~~~~~~~~

.. automodule:: sql.adapters.base
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: sql.adapters.sqlite
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: sql.adapters.postgresql
   :members:
   :undoc-members:
   :show-inheritance:


Storage Package
---------------

Storage node management for multi-backend file storage.

.. automodule:: storage.manager
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: storage.node
   :members:
   :undoc-members:
   :show-inheritance:


Tools Package
-------------

Shared utilities used across packages.

.. automodule:: tools.encryption
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: tools.http_client.client
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: tools.prometheus.metrics
   :members:
   :undoc-members:
   :show-inheritance:

.. automodule:: tools.repl
   :members:
   :undoc-members:
   :show-inheritance:
