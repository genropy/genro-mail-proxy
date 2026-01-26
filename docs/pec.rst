PEC Support (Posta Elettronica Certificata)
===========================================

.. note::

   PEC support is an Enterprise feature available under the Business Source License 1.1.
   See ``LICENSE-BSL-1.1`` for details.

This document describes PEC (Posta Elettronica Certificata) support in genro-mail-proxy,
the Italian certified email system that provides legal proof of delivery.

Overview
--------

PEC (Posta Elettronica Certificata) is Italy's legally binding certified email system.
When you send a PEC message, the PEC provider issues receipts (ricevute) that prove:

* **Acceptance** (accettazione): The PEC system accepted your message
* **Delivery** (avvenuta consegna): The message was delivered to the recipient's mailbox
* **Non-delivery** (mancata consegna): The message could not be delivered

These receipts have legal value equivalent to registered mail (raccomandata A/R).

genro-mail-proxy extends its delivery tracking to support PEC-specific receipts,
providing a complete audit trail for certified email communications.

PEC Message Flow
----------------

.. code-block:: text

   ┌─────────────┐      REST       ┌──────────────────┐      SMTP      ┌─────────────┐
   │ Application │ ──────────────► │ genro-mail-proxy │ ─────────────► │ PEC Server  │
   └─────────────┘                 └──────────────────┘                └─────────────┘
                                          │                                   │
                                          │                                   │
                                          │ IMAP polling                      │
                                          │◄────────────────────────────────  │
                                          │     (ricevute PEC)                │
                                          │                                   │
          ▲                               │
          │                               │
          └───────────────────────────────┘
                   PEC events callback

**Flow steps:**

1. Application submits message via REST API to a PEC account
2. Proxy sends message via SMTP to PEC provider
3. Proxy polls PEC account's IMAP inbox for receipts
4. Proxy parses receipts and records PEC events
5. PEC events are reported back to the application

PEC Receipt Types
-----------------

The proxy recognizes these Italian PEC receipt types:

.. list-table::
   :header-rows: 1

   * - Receipt Type
     - Italian Name
     - Description
     - Event Type
   * - ``accettazione``
     - Ricevuta di accettazione
     - Message accepted by sender's PEC provider
     - ``pec_acceptance``
   * - ``avvenuta-consegna``
     - Ricevuta di avvenuta consegna
     - Message delivered to recipient's mailbox
     - ``pec_delivery``
   * - ``mancata-consegna``
     - Ricevuta di mancata consegna
     - Delivery failed (recipient not found, mailbox full, etc.)
     - ``pec_error``
   * - ``non-accettazione``
     - Ricevuta di non accettazione
     - Message rejected by sender's PEC provider
     - ``pec_error``
   * - ``presa-in-carico``
     - Ricevuta di presa in carico
     - Message handed off to recipient's PEC provider
     - ``pec_relay``

PEC Account Configuration
-------------------------

To enable PEC support, create an account with ``is_pec_account: true`` and
configure IMAP settings for receipt polling:

.. code-block:: bash

   curl -X POST http://localhost:8000/account \
     -H "Content-Type: application/json" \
     -H "X-API-Token: your-api-token" \
     -d '{
       "id": "pec-acme",
       "tenant_id": "acme",
       "host": "smtp.pec.provider.it",
       "port": 465,
       "user": "acme@pec.provider.it",
       "password": "smtp-password",
       "use_tls": true,
       "is_pec_account": true,
       "imap_host": "imap.pec.provider.it",
       "imap_port": 993,
       "imap_user": "acme@pec.provider.it",
       "imap_password": "imap-password"
     }'

**PEC-specific fields:**

.. list-table::
   :header-rows: 1

   * - Field
     - Type
     - Default
     - Description
   * - ``is_pec_account``
     - ``bool``
     - ``false``
     - Mark this account as PEC-enabled
   * - ``imap_host``
     - ``str``
     - -
     - IMAP server for receipt polling
   * - ``imap_port``
     - ``int``
     - ``993``
     - IMAP port (usually 993 for IMAPS)
   * - ``imap_user``
     - ``str``
     - SMTP user
     - IMAP username (defaults to SMTP user)
   * - ``imap_password``
     - ``str``
     - SMTP password
     - IMAP password (defaults to SMTP password)
   * - ``imap_folder``
     - ``str``
     - ``INBOX``
     - Folder to monitor for receipts

Message Correlation
-------------------

PEC receipts are correlated with original messages using the ``X-Genro-Mail-ID``
header. When sending messages through PEC accounts:

1. The proxy adds ``X-Genro-Mail-ID: <message-id>`` to outgoing messages
2. Italian PEC providers include this header in their receipts
3. The proxy extracts the header from receipts to identify the original message

.. code-block:: text

   Original message:
   X-Genro-Mail-ID: acme-msg-001

   Receipt (accettazione):
   X-Genro-Mail-ID: acme-msg-001
   X-Ricevuta: accettazione

This ensures reliable correlation even when the PEC provider modifies Message-ID.

PEC Events
----------

PEC receipts generate events in the ``message_events`` table:

.. code-block:: json

   {
     "event_type": "pec_delivery",
     "event_ts": 1705750800,
     "event_data": {
       "receipt_type": "avvenuta-consegna",
       "recipient": "dest@pec.it",
       "receipt_message_id": "<receipt-123@pec.provider.it>"
     }
   }

**Event types:**

* ``pec_acceptance``: Acceptance receipt received
* ``pec_delivery``: Delivery confirmation received
* ``pec_error``: Delivery failure or rejection receipt
* ``pec_relay``: Handoff confirmation (presa-in-carico)
* ``pec_timeout``: No acceptance receipt within timeout period

Timeout Detection
-----------------

If a PEC message doesn't receive an acceptance receipt within the configured
timeout (default: 30 minutes), it's flagged as potentially problematic:

* Messages without ``pec_acceptance`` event after timeout get a ``pec_timeout`` event
* This helps identify delivery issues that might otherwise go unnoticed
* The timeout is configurable via ``GMP_PEC_ACCEPTANCE_TIMEOUT`` (seconds)

.. code-block:: bash

   # Set PEC acceptance timeout to 1 hour
   export GMP_PEC_ACCEPTANCE_TIMEOUT=3600

API Endpoints
-------------

``GET /messages/{message_id}/events``
   Get all events for a message, including PEC receipts.

   Response:

   .. code-block:: json

      {
        "ok": true,
        "events": [
          {
            "event_type": "queued",
            "event_ts": 1705750000,
            "event_data": {}
          },
          {
            "event_type": "sent",
            "event_ts": 1705750100,
            "event_data": {"smtp_response": "250 OK"}
          },
          {
            "event_type": "pec_acceptance",
            "event_ts": 1705750150,
            "event_data": {"receipt_type": "accettazione"}
          },
          {
            "event_type": "pec_delivery",
            "event_ts": 1705750800,
            "event_data": {
              "receipt_type": "avvenuta-consegna",
              "recipient": "dest@pec.it"
            }
          }
        ]
      }

``GET /accounts``
   List accounts. PEC accounts include ``is_pec_account: true``.

   Response includes:

   .. code-block:: json

      {
        "accounts": [
          {
            "id": "pec-acme",
            "tenant_id": "acme",
            "host": "smtp.pec.provider.it",
            "port": 465,
            "is_pec_account": true,
            "imap_host": "imap.pec.provider.it",
            "imap_port": 993
          }
        ]
      }

Delivery Report Integration
---------------------------

PEC events are delivered to tenants via the bidirectional sync protocol
(see :doc:`protocol` for the complete specification). When the proxy calls
the tenant's sync endpoint, PEC events are included in the ``delivery_report``:

.. code-block:: json

   {
     "delivery_report": [
       {
         "id": "acme-msg-001",
         "pec_event": "pec_acceptance",
         "pec_ts": 1705750150,
         "pec_details": "Accepted by provider"
       },
       {
         "id": "acme-msg-001",
         "pec_event": "pec_delivery",
         "pec_ts": 1705750800,
         "pec_details": "Delivered to recipient"
       }
     ]
   }

**PEC event fields in delivery reports:**

.. list-table::
   :header-rows: 1

   * - Field
     - Type
     - Description
   * - ``pec_event``
     - str
     - Event type: ``pec_acceptance``, ``pec_delivery``, ``pec_error``, ``pec_relay``
   * - ``pec_ts``
     - int
     - Unix timestamp when PEC event was recorded
   * - ``pec_details``
     - str
     - Additional information from the PEC receipt

**Sync protocol for PEC:**

The sync protocol is particularly important for PEC because:

1. **Acceptance receipts** (ricevuta di accettazione) arrive within minutes of sending
2. **Delivery receipts** (ricevuta di consegna) arrive when the recipient's PEC server accepts

The proxy reports these events as they arrive. The client can use the ``queued``
response field to signal it has new PEC messages to send, triggering an
accelerated sync cycle. See :doc:`protocol` for details on the sync loop.

Client implementation example for PEC
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @app.route("/proxy_sync", methods=["POST"])
   def proxy_sync():
       data = request.json
       reports = data.get("delivery_report", [])

       for report in reports:
           msg_id = report["id"]

           # Handle PEC-specific events
           if "pec_event" in report:
               pec_event = report["pec_event"]
               pec_ts = report["pec_ts"]

               if pec_event == "pec_acceptance":
                   # Legal proof: message accepted by PEC system
                   record_pec_acceptance(msg_id, pec_ts)
               elif pec_event == "pec_delivery":
                   # Legal proof: message delivered to recipient
                   record_pec_delivery(msg_id, pec_ts)
               elif pec_event == "pec_error":
                   # Delivery failed - needs attention
                   record_pec_failure(msg_id, report.get("pec_details"))

           # Handle standard delivery events
           elif "sent_ts" in report:
               mark_as_sent(msg_id, report["sent_ts"])
           elif "error_ts" in report:
               mark_as_failed(msg_id, report["error"])

       # Check for pending PEC messages to send
       pending_pec = get_pending_pec_messages()
       return jsonify({"ok": True, "queued": len(pending_pec)})

Italian PEC Providers
---------------------

Common Italian PEC providers and their server settings:

.. list-table::
   :header-rows: 1

   * - Provider
     - SMTP Host
     - SMTP Port
     - IMAP Host
     - IMAP Port
   * - Aruba PEC
     - smtps.pec.aruba.it
     - 465
     - imaps.pec.aruba.it
     - 993
   * - Legalmail
     - sendm.legalmail.it
     - 465
     - mbox.legalmail.it
     - 993
   * - PEC.it
     - smtps.pec.it
     - 465
     - imaps.pec.it
     - 993
   * - Register.it
     - smtp.pec.register.it
     - 465
     - imap.pec.register.it
     - 993

.. note::

   Always verify current server settings with your PEC provider,
   as they may change over time.

Best Practices
--------------

1. **Use separate accounts for PEC**: Don't mix PEC and regular email on the same account

2. **Monitor acceptance timeouts**: Set up alerts for ``pec_timeout`` events

3. **Archive receipts**: PEC receipts have legal value - ensure proper backup

4. **Handle failures promptly**: ``mancata-consegna`` requires action (verify address, retry)

5. **Test with sandbox**: Most PEC providers offer test environments

Complete Example
----------------

1. **Configure PEC account:**

   .. code-block:: bash

      curl -X POST http://localhost:8000/account \
        -H "Content-Type: application/json" \
        -H "X-API-Token: your-api-token" \
        -d '{
          "id": "pec-legal",
          "tenant_id": "acme",
          "host": "smtps.pec.aruba.it",
          "port": 465,
          "user": "acme@pec.it",
          "password": "secret",
          "use_tls": true,
          "is_pec_account": true,
          "imap_host": "imaps.pec.aruba.it",
          "imap_port": 993
        }'

2. **Send certified message:**

   .. code-block:: bash

      curl -X POST http://localhost:8000/commands/add-messages \
        -H "Content-Type: application/json" \
        -H "X-API-Token: your-api-token" \
        -d '{
          "messages": [{
            "id": "legal-notice-001",
            "account_id": "pec-legal",
            "from": "acme@pec.it",
            "to": ["destinatario@pec.it"],
            "subject": "Comunicazione legale",
            "body": "Testo della comunicazione certificata."
          }]
        }'

3. **Check PEC events:**

   .. code-block:: bash

      curl http://localhost:8000/messages/legal-notice-001/events \
        -H "X-API-Token: your-api-token"

4. **Expected timeline:**

   * ``queued``: Immediate
   * ``sent``: Within seconds
   * ``pec_acceptance``: Within minutes (or ``pec_timeout`` after 30 min)
   * ``pec_delivery``: Varies (depends on recipient's PEC provider)
