
Express.js Integration
======================

This guide shows how to integrate genro-mail-proxy with an Express.js (Node.js)
application.

The standard Node.js email library is Nodemailer. It works well for simple cases
but has limitations:

- Synchronous API (callbacks or promises, but blocks the event loop during SMTP)
- No built-in queue or retry mechanism
- No delivery tracking beyond the initial send result
- Rate limiting requires manual implementation

For production use, you typically add Bull/BullMQ (Redis-based queue), which
adds infrastructure complexity similar to Python's Celery.

genro-mail-proxy provides queuing, retry, and delivery reports as a standalone
service, accessible via simple HTTP calls from any Node.js application.

When to use the proxy with Express
----------------------------------

Consider genro-mail-proxy when:

- You don't want to introduce Redis + Bull just for email
- You need delivery reports with automatic callback
- Multiple services (Node.js, Python, etc.) share email infrastructure
- You need rate limiting shared across application instances

When Nodemailer is sufficient:

- Low volume, non-critical emails
- Immediate send with no retry requirements
- Single instance with no rate limiting needs

Comparison with Nodemailer + Bull
---------------------------------

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Feature
     - Nodemailer + Bull
     - genro-mail-proxy
   * - Dependencies
     - Redis + Bull worker
     - Only the proxy
   * - Delivery reports
     - Manual implementation
     - Built-in HTTP callback
   * - Retry on failure
     - Bull retry options
     - Built-in exponential backoff
   * - Rate limiting
     - Bull rate limiter
     - Built-in per account
   * - Language agnostic
     - No (Node.js only)
     - Yes (HTTP API)
   * - Operational complexity
     - Medium (Redis + worker)
     - Medium (one service)

Installation
------------

.. code-block:: bash

   npm install axios  # or use native fetch in Node 18+

Configuration
-------------

.. code-block:: javascript

   // config.js
   module.exports = {
     mailProxy: {
       url: process.env.MAIL_PROXY_URL || 'http://localhost:8000',
       token: process.env.MAIL_PROXY_TOKEN || 'your-api-token',
       accountId: process.env.MAIL_PROXY_ACCOUNT || 'default',
     },
   };

Client module
-------------

.. code-block:: javascript

   // mailProxy.js
   const axios = require('axios');
   const { v4: uuidv4 } = require('uuid');
   const config = require('./config');

   class MailProxyClient {
     constructor() {
       this.baseUrl = config.mailProxy.url;
       this.token = config.mailProxy.token;
       this.accountId = config.mailProxy.accountId;
     }

     /**
      * Send an email through the mail proxy.
      *
      * @param {Object} options - Email options
      * @param {string} options.subject - Email subject
      * @param {string} options.body - Plain text body
      * @param {string} options.from - Sender address
      * @param {string|string[]} options.to - Recipient(s)
      * @param {string|string[]} [options.cc] - CC recipient(s)
      * @param {string|string[]} [options.bcc] - BCC recipient(s)
      * @param {string} [options.html] - HTML body
      * @param {Object[]} [options.attachments] - Attachments
      * @param {number} [options.priority=2] - Priority (0-3)
      * @param {string} [options.messageId] - Custom message ID
      * @returns {Promise<Object>} Response with queued/rejected counts
      */
     async sendMail({
       subject,
       body,
       from,
       to,
       cc,
       bcc,
       html,
       attachments,
       priority = 2,
       messageId,
     }) {
       const message = {
         id: messageId || uuidv4(),
         account_id: this.accountId,
         from,
         to: Array.isArray(to) ? to : [to],
         subject,
         body: html || body,
         content_type: html ? 'html' : 'plain',
         priority,
       };

       if (cc) {
         message.cc = Array.isArray(cc) ? cc : [cc];
       }
       if (bcc) {
         message.bcc = Array.isArray(bcc) ? bcc : [bcc];
       }
       if (attachments) {
         message.attachments = attachments;
       }

       const response = await axios.post(
         `${this.baseUrl}/commands/add-messages`,
         { messages: [message] },
         {
           headers: {
             'X-API-Token': this.token,
             'Content-Type': 'application/json',
           },
           timeout: 10000,
         }
       );

       return response.data;
     }
   }

   module.exports = new MailProxyClient();

Express application
-------------------

.. code-block:: javascript

   // app.js
   const express = require('express');
   const mailProxy = require('./mailProxy');

   const app = express();
   app.use(express.json());

   // Send welcome email
   app.post('/send-welcome/:userId', async (req, res, next) => {
     try {
       const user = await getUser(req.params.userId);

       const result = await mailProxy.sendMail({
         subject: `Welcome ${user.name}!`,
         body: `Hello ${user.name}, thanks for signing up.`,
         from: 'noreply@example.com',
         to: user.email,
         priority: 1,
       });

       res.json(result);
     } catch (err) {
       next(err);
     }
   });

   // Send invoice with attachment
   app.post('/send-invoice/:invoiceId', async (req, res, next) => {
     try {
       const invoice = await getInvoice(req.params.invoiceId);

       const result = await mailProxy.sendMail({
         subject: `Invoice #${invoice.number}`,
         body: `Please find attached invoice #${invoice.number}.`,
         html: `<p>Please find attached invoice <strong>#${invoice.number}</strong>.</p>`,
         from: 'billing@example.com',
         to: invoice.customerEmail,
         attachments: [
           {
             filename: `invoice_${invoice.number}.pdf`,
             storage_path: `invoice_id=${invoice.id}`,
             fetch_mode: 'endpoint',
           },
         ],
       });

       res.json(result);
     } catch (err) {
       next(err);
     }
   });

   app.listen(3000);

Delivery reports endpoint
-------------------------

.. code-block:: javascript

   // app.js (continued)

   // Receive delivery reports from the mail proxy
   app.post('/mail/delivery-report', (req, res) => {
     const { delivery_report: reports = [] } = req.body;

     let sent = 0;
     let error = 0;

     for (const report of reports) {
       const { id: messageId, sent_ts, error_ts, error: errorMsg } = report;

       if (sent_ts) {
         sent++;
         // Update database
         // await markEmailSent(messageId, sent_ts);
       } else if (error_ts) {
         error++;
         // Log error
         // await markEmailFailed(messageId, errorMsg);
       }
     }

     res.json({ ok: true, queued: 0 });
   });

   // Serve attachments to the mail proxy
   app.post('/mail/attachments', async (req, res) => {
     const { invoice_id: invoiceId } = req.body;

     if (invoiceId) {
       const invoice = await getInvoice(invoiceId);
       const pdfBuffer = await generateInvoicePdf(invoice);

       res.set('Content-Type', 'application/pdf');
       res.send(pdfBuffer);
       return;
     }

     res.status(404).send();
   });

TypeScript version
------------------

.. code-block:: typescript

   // mailProxy.ts
   import axios from 'axios';
   import { v4 as uuidv4 } from 'uuid';

   interface MailOptions {
     subject: string;
     body: string;
     from: string;
     to: string | string[];
     cc?: string | string[];
     bcc?: string | string[];
     html?: string;
     attachments?: Attachment[];
     priority?: number;
     messageId?: string;
   }

   interface Attachment {
     filename: string;
     storage_path: string;
     fetch_mode?: string;
   }

   interface SendResult {
     queued: number;
     rejected: Array<{ id: string; reason: string }>;
   }

   class MailProxyClient {
     private baseUrl: string;
     private token: string;
     private accountId: string;

     constructor() {
       this.baseUrl = process.env.MAIL_PROXY_URL || 'http://localhost:8000';
       this.token = process.env.MAIL_PROXY_TOKEN || '';
       this.accountId = process.env.MAIL_PROXY_ACCOUNT || 'default';
     }

     async sendMail(options: MailOptions): Promise<SendResult> {
       const {
         subject,
         body,
         from,
         to,
         cc,
         bcc,
         html,
         attachments,
         priority = 2,
         messageId,
       } = options;

       const message: Record<string, unknown> = {
         id: messageId || uuidv4(),
         account_id: this.accountId,
         from,
         to: Array.isArray(to) ? to : [to],
         subject,
         body: html || body,
         content_type: html ? 'html' : 'plain',
         priority,
       };

       if (cc) message.cc = Array.isArray(cc) ? cc : [cc];
       if (bcc) message.bcc = Array.isArray(bcc) ? bcc : [bcc];
       if (attachments) message.attachments = attachments;

       const response = await axios.post<SendResult>(
         `${this.baseUrl}/commands/add-messages`,
         { messages: [message] },
         {
           headers: {
             'X-API-Token': this.token,
             'Content-Type': 'application/json',
           },
           timeout: 10000,
         }
       );

       return response.data;
     }
   }

   export default new MailProxyClient();

Nodemailer comparison
---------------------

For reference, here's how Nodemailer with Bull looks:

.. code-block:: javascript

   // With Nodemailer + Bull
   const nodemailer = require('nodemailer');
   const Queue = require('bull');

   const transporter = nodemailer.createTransport({
     host: 'smtp.example.com',
     port: 587,
     auth: { user: 'user', pass: 'password' },
   });

   const emailQueue = new Queue('email', 'redis://localhost:6379');

   emailQueue.process(async (job) => {
     await transporter.sendMail(job.data);
     // No automatic delivery report to your app
   });

   // Usage
   await emailQueue.add({
     from: 'noreply@example.com',
     to: user.email,
     subject: 'Welcome!',
     text: 'Hello',
   });

This requires running a Redis server. The mail proxy consolidates queuing,
retry, and delivery tracking into a single service.

Using native fetch (Node 18+)
-----------------------------

If you prefer not to use axios:

.. code-block:: javascript

   // mailProxy.js (native fetch version)
   const { v4: uuidv4 } = require('uuid');

   class MailProxyClient {
     constructor() {
       this.baseUrl = process.env.MAIL_PROXY_URL || 'http://localhost:8000';
       this.token = process.env.MAIL_PROXY_TOKEN || '';
       this.accountId = process.env.MAIL_PROXY_ACCOUNT || 'default';
     }

     async sendMail({ subject, body, from, to, html, priority = 2, messageId }) {
       const message = {
         id: messageId || uuidv4(),
         account_id: this.accountId,
         from,
         to: Array.isArray(to) ? to : [to],
         subject,
         body: html || body,
         content_type: html ? 'html' : 'plain',
         priority,
       };

       const response = await fetch(`${this.baseUrl}/commands/add-messages`, {
         method: 'POST',
         headers: {
           'X-API-Token': this.token,
           'Content-Type': 'application/json',
         },
         body: JSON.stringify({ messages: [message] }),
       });

       if (!response.ok) {
         throw new Error(`Mail proxy error: ${response.status}`);
       }

       return response.json();
     }
   }

   module.exports = new MailProxyClient();

Proxy tenant configuration
--------------------------

Configure the proxy tenant to point to your Express endpoints:

.. code-block:: bash

   mail-proxy myserver tenants add myexpressapp \
       --base-url "https://myexpressapp.example.com" \
       --sync-path "/mail/delivery-report" \
       --attachment-path "/mail/attachments" \
       --auth-method bearer \
       --auth-token "shared-secret"
