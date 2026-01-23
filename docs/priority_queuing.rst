
Priority Queuing
================

genro-mail-proxy supports four priority levels for message ordering.
Higher-priority messages are always processed before lower-priority ones.

Priority Levels
---------------

.. list-table::
   :header-rows: 1
   :widths: 15 15 70

   * - Value
     - Label
     - Description
   * - 0
     - ``immediate``
     - Processed first in a separate pass. Use for urgent notifications.
   * - 1
     - ``high``
     - Processed before medium and low. Use for time-sensitive emails.
   * - 2
     - ``medium``
     - Default priority. Use for regular transactional emails.
   * - 3
     - ``low``
     - Processed last. Use for bulk emails, newsletters, digests.

Within the same priority level, messages are processed in **FIFO order**
(oldest first).


How It Works
------------

The dispatcher processes messages in two passes:

1. **Immediate pass**: All priority=0 messages are fetched and dispatched
2. **Regular pass**: Messages with priority >= 1 are fetched in priority order

This ensures truly urgent messages get immediate processing, even when the
queue has many pending regular messages.

.. code-block:: text

   Queue: [low, medium, immediate, high, medium, immediate, low]

   Pass 1: immediate, immediate
   Pass 2: high, medium, medium, low, low

   Send order: immediate → immediate → high → medium → medium → low → low


Setting Message Priority
------------------------

**Via REST API**:

.. code-block:: bash

   curl -X POST http://localhost:8000/commands/add-messages \
     -H "X-API-Token: $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "messages": [
         {
           "id": "urgent-alert-123",
           "account_id": "primary-smtp",
           "from": "alerts@example.com",
           "to": ["admin@example.com"],
           "subject": "Server Down!",
           "body": "Production server is unreachable.",
           "priority": "immediate"
         }
       ]
     }'

**Via Python Client**:

.. code-block:: python

   from mail_proxy.client import MailProxyClient

   client = MailProxyClient("http://localhost:8000", token="your-token")

   # Queue an urgent message
   client.messages.enqueue({
       "id": "urgent-alert-123",
       "account_id": "primary-smtp",
       "from": "alerts@example.com",
       "to": ["admin@example.com"],
       "subject": "Server Down!",
       "body": "Production server is unreachable.",
       "priority": "immediate",  # or 0
   })


Priority can be specified as:

- **String**: ``"immediate"``, ``"high"``, ``"medium"``, ``"low"``
- **Integer**: ``0``, ``1``, ``2``, ``3``

Both are equivalent—use whichever is more readable in your code.


Batch Default Priority
----------------------

When enqueuing multiple messages, you can set a default priority for the
batch and override per-message:

.. code-block:: bash

   curl -X POST http://localhost:8000/commands/add-messages \
     -H "X-API-Token: $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "default_priority": "low",
       "messages": [
         {
           "id": "newsletter-1",
           "account_id": "bulk-smtp",
           "from": "news@example.com",
           "to": ["user1@example.com"],
           "subject": "Weekly Update",
           "body": "Here is your weekly digest..."
         },
         {
           "id": "newsletter-2",
           "account_id": "bulk-smtp",
           "from": "news@example.com",
           "to": ["user2@example.com"],
           "subject": "Weekly Update",
           "body": "Here is your weekly digest..."
         },
         {
           "id": "urgent-notice",
           "account_id": "bulk-smtp",
           "from": "news@example.com",
           "to": ["vip@example.com"],
           "subject": "Important Update",
           "body": "Critical information...",
           "priority": "high"
         }
       ]
     }'

In this example, ``newsletter-1`` and ``newsletter-2`` get ``low`` priority
(from the batch default), while ``urgent-notice`` gets ``high`` priority
(explicit override).


Use Cases
---------

**Immediate (priority=0)**

- System alerts (server down, security breach)
- Password reset emails
- Two-factor authentication codes
- Payment confirmations

.. code-block:: python

   # Password reset - needs immediate delivery
   client.messages.enqueue({
       "id": f"reset-{user_id}",
       "priority": "immediate",
       "subject": "Reset Your Password",
       "body": f"Click here: {reset_link}",
       # ...
   })

**High (priority=1)**

- Order confirmations
- Shipping notifications
- Appointment reminders (same day)
- Welcome emails

.. code-block:: python

   # Order confirmation - important but not critical
   client.messages.enqueue({
       "id": f"order-{order_id}",
       "priority": "high",
       "subject": f"Order #{order_id} Confirmed",
       # ...
   })

**Medium (priority=2, default)**

- General transactional emails
- Account notifications
- Activity summaries
- Standard customer communication

.. code-block:: python

   # No priority specified = medium (default)
   client.messages.enqueue({
       "id": f"activity-{user_id}",
       "subject": "Your Weekly Activity",
       # ...
   })

**Low (priority=3)**

- Marketing newsletters
- Promotional emails
- Digest emails
- Non-urgent notifications

.. code-block:: python

   # Bulk newsletter - can wait
   for user in users:
       client.messages.enqueue({
           "id": f"newsletter-{user.id}",
           "priority": "low",
           "subject": "Monthly Newsletter",
           # ...
       })


Instance Default Priority
-------------------------

You can set a default priority at the instance level when starting the
service. Messages without explicit priority will use this default:

**Via Python**:

.. code-block:: python

   from mail_proxy import MailProxy

   proxy = MailProxy(
       db_path="./mail_service.db",
       default_priority="low",  # All messages default to low
   )

**Precedence**: Message priority > Batch default > Instance default


Interaction with Rate Limiting
------------------------------

Priority affects **processing order**, not rate limits. A high-priority
message will be processed before low-priority ones, but if the account's
rate limit is reached, even high-priority messages will be deferred.

.. code-block:: text

   Rate limit: 10/minute
   Queue: [immediate, immediate, high, high, medium, medium, ...]

   If 10 messages sent this minute:
   - immediate messages are deferred (not skipped)
   - They'll be first in line when the limit resets


Monitoring Priority Distribution
--------------------------------

Check queue composition by priority:

.. code-block:: bash

   # Count messages by priority
   curl "http://localhost:8000/messages?tenant_id=acme" \
     -H "X-API-Token: $TOKEN" | \
     jq 'group_by(.priority) | map({priority: .[0].priority, count: length})'

Output:

.. code-block:: json

   [
     {"priority": 0, "count": 2},
     {"priority": 1, "count": 15},
     {"priority": 2, "count": 150},
     {"priority": 3, "count": 5000}
   ]


Best Practices
--------------

1. **Use immediate sparingly**: Reserve for truly urgent messages. Overuse
   defeats the purpose.

2. **Default to medium**: Most transactional emails should be medium priority.
   Only elevate when there's a real time constraint.

3. **Batch bulk at low**: Newsletters and marketing should always be low
   priority to avoid blocking transactional emails.

4. **Monitor queue depth by priority**: If high-priority messages accumulate,
   your throughput may be insufficient.

5. **Consider separate accounts**: For very different workloads, separate
   SMTP accounts with independent rate limits may work better than priorities.


See Also
--------

- :doc:`rate_limiting` for understanding how rate limits interact with priorities
- :doc:`usage` for configuration options
- :doc:`api_reference` for the full message payload schema
