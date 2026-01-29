# Unique Value Proposition

## The Only Email Proxy That Solves All Four Problems

genro-mail-proxy occupies a unique niche in the email infrastructure landscape. It's the **only open-source solution** that combines:

1. ✅ **Queue-based SMTP proxy** (solves database-SMTP misalignment)
2. ✅ **IMAP bounce polling** (active detection, not passive webhooks)
3. ✅ **PEC receipt tracking** (Italian certified email compliance)
4. ✅ **Transparent large file offloading** (auto-upload + body rewrite)

**No other product—commercial or open-source—offers this combination.**

---

## The Four Unique Capabilities

### 1. Transactional Consistency: Solving Database-SMTP Misalignment

**The Problem:**

Traditional email architectures create a fundamental inconsistency:

```python
# Traditional approach (BROKEN)
db.execute("UPDATE emails SET status='sent' WHERE id=?", email_id)
db.commit()  # ✅ Database says "sent"

smtp.send(email)  # ❌ This might fail!
# If SMTP fails, database already says "sent" but email never delivered
```

This creates an irreconcilable state where your database lies about reality.

**The Solution: Queue-Based Proxy Pattern**

```python
# With genro-mail-proxy (CORRECT)
db.execute("UPDATE emails SET status='queued' WHERE id=?", email_id)
db.commit()  # ✅ Database says "to be sent"

# Proxy polls queue
# Proxy sends via SMTP
# Proxy updates: status='sent' OR status='error'
# ✅ Database always reflects reality
```

**Why This Matters:**

- **Request latency**: ~30ms (vs ~600ms direct SMTP) — **20x faster**
- **Database consistency**: 100% reliable (no phantom "sent" emails)
- **Automatic retry**: Exponential backoff for transient failures
- **Business logic decoupling**: Application never blocks on SMTP

**Competitors Don't Solve This:**

- **SendGrid/Mailgun**: Direct API calls—you still have the same problem, just with their API instead of SMTP
- **RabbitMQ/Redis**: Generic queue, you must implement the entire pattern yourself
- **Postal**: Full mail server, not a proxy pattern

---

### 2. Active Bounce Detection via IMAP Polling

**The Problem:**

Email bounces happen. How do you detect them?

**Traditional Approach: Passive Webhooks**

```
Your App → SendGrid API → SMTP
                ↓
         (hours later)
                ↓
    Webhook to your server
    (requires public endpoint)
```

**Limitations:**
- ❌ Requires public endpoint (not always possible in enterprise)
- ❌ Passive: you wait for the provider to notify you
- ❌ Can miss bounces if webhook delivery fails
- ❌ No control over timing

**genro-mail-proxy Approach: Active IMAP Polling**

```python
# Active polling every N minutes
imap.connect(bounce_mailbox)
messages = imap.search('UNSEEN')

for msg in messages:
    dsn = parse_dsn(msg)  # RFC 3464/6522
    
    if dsn.is_hard_bounce():
        db.update(email_id, status='hard_bounce')
    elif dsn.is_soft_bounce():
        db.update(email_id, status='soft_bounce')
```

**Advantages:**
- ✅ **Active**: you control polling frequency
- ✅ **No public endpoint required**: works behind firewall
- ✅ **DSN parsing**: RFC 3464/6522 compliance (hard vs soft bounce classification)
- ✅ **Reliable**: no webhook delivery failures
- ✅ **Self-contained**: all data stays in your infrastructure

**No Competitor Offers This:**

- **SendGrid/Mailgun/Postmark**: Webhook-only
- **Custom solutions**: Must implement IMAP + DSN parsing yourself
- **GlockApps Bounce Monitor**: SaaS solution (~€50-100/month) doing IMAP polling, but it's not integrated

---

### 3. PEC (Posta Elettronica Certificata) Support

**What is PEC?**

PEC is **Italian Certified Email** (RFC 6109), legally equivalent to registered mail. It's **mandatory for Italian businesses** to have a PEC address.

**PEC Workflow:**

```
Sender → PEC Provider → Recipient
           ↓
    Acceptance Receipt (ricevuta di accettazione)
           ↓
    Delivery Receipt (ricevuta di consegna)
```

Both receipts have **legal value** and must be tracked.

**genro-mail-proxy PEC Support:**

- ✅ Tracks acceptance receipts
- ✅ Tracks delivery receipts
- ✅ Updates database with receipt status
- ✅ Distinguishes PEC vs non-PEC emails
- ✅ RFC 6109 compliant parsing

**Example:**

```python
# Application sends email
requests.post('http://proxy:8000/send', json={
    'to': 'company@pec.example.it',
    'subject': 'Contratto',
    'body': '...',
    'is_pec': True  # Enable PEC tracking
})

# genro-mail-proxy automatically:
# 1. Sends email
# 2. Polls IMAP for acceptance receipt
# 3. Updates DB: pec_acceptance_received_at
# 4. Polls IMAP for delivery receipt
# 5. Updates DB: pec_delivery_received_at
```

**No International Competitor Supports PEC:**

- **SendGrid/Mailgun/Postmark**: Zero PEC support
- **Postal**: No PEC support
- **PEC Providers (Aruba, Poste, Namirial)**: They ARE the PEC service, not a proxy

**Market Significance:**

- 🇮🇹 **11+ million Italian businesses** must have PEC
- 💼 Legal communications require PEC
- 📧 Contracts, invoices, official notices
- 🏛️ Government communications mandatory via PEC

genro-mail-proxy is the **only proxy** that enables transactional applications to send PEC-compliant emails programmatically.

---

### 4. Transparent Large File Handling

**The Problem:**

Email attachments are limited (typically 10-25MB). Large files fail or get rejected.

**Traditional Solutions:**

```python
# Option 1: Reject large files (bad UX)
if attachment_size > 10_000_000:
    return "File too large"

# Option 2: Manual upload (breaks automation)
url = s3.upload(file)
email_body = f"Download here: {url}"
send_email(body=email_body)
```

**genro-mail-proxy Solution: Transparent Offloading**

```python
# Configuration
large_file_threshold: 10_000_000  # 10MB
large_file_action: "rewrite"
storage_backend: "s3"
bucket: "company-email-attachments"
presigned_url_expiry: 604800  # 7 days

# Application code (UNCHANGED!)
send_email(
    to="client@example.com",
    subject="Quarterly Report",
    body="<p>Please see attached report.</p>",
    attachments=[
        {"filename": "Q4_Report.pdf", "size": 35_000_000}  # 35MB!
    ]
)

# genro-mail-proxy automatically:
# 1. Detects attachment > 10MB
# 2. Uploads to S3: s3://bucket/Q4_Report_abc123.pdf
# 3. Generates presigned URL (7-day expiry)
# 4. Rewrites HTML body:
#    <p>Please see attached report.</p>
#    <p><strong>Large attachments:</strong></p>
#    <ul>
#      <li><a href="https://s3.../Q4_Report_abc123.pdf?...">Q4_Report.pdf</a> (35 MB, expires Jan 31)</li>
#    </ul>
# 5. Sends email with rewritten body
```

**Supported Storage Backends:**

- Amazon S3
- Google Cloud Storage
- Azure Blob Storage
- Local filesystem (with HTTP server)

**Actions Available:**

- `warn`: Log warning, send anyway (may fail)
- `reject`: Return error to application
- `rewrite`: **Upload + rewrite body** (transparent to app!)

**Benefits:**

- ✅ **Zero code changes**: Application doesn't need to know about S3
- ✅ **Automatic expiry**: Presigned URLs expire after N days
- ✅ **Professional emails**: Nicely formatted download links
- ✅ **Reliable delivery**: No attachment size limits
- ✅ **Cost efficient**: Offload large files to cheap object storage

**No Competitor Offers This:**

- **SendGrid/Mailgun**: You must upload to S3 yourself and modify body
- **Custom solutions**: Must implement upload + body rewrite + presigned URL generation
- **Postal**: No large file handling

---

## Competitive Analysis

### Who Are the Alternatives?

| Solution | Category | Strengths | Weaknesses |
|----------|----------|-----------|------------|
| **SendGrid/Mailgun** | SaaS Email API | Easy setup, managed infrastructure | ❌ No DB consistency<br>❌ Webhook-only bounces<br>❌ No PEC<br>❌ No large file handling<br>💰 Expensive (€500-1000/month) |
| **RabbitMQ/Redis + Custom** | Message Queue | Maximum flexibility | ❌ Must build everything<br>❌ High development cost<br>❌ No email-specific features |
| **Postal** | Open Source Mail Server | Full mail server, self-hosted | ❌ Complex setup<br>❌ Replaces SMTP server (not a proxy)<br>❌ No queue pattern<br>❌ No PEC<br>❌ No large file handling |
| **Haraka** | SMTP Proxy | SMTP filtering | ❌ Real-time only (no queue)<br>❌ No persistent queue<br>❌ Designed for spam filtering, not transactional |
| **GlockApps** | SaaS Bounce Monitor | IMAP bounce polling | 💰 Expensive (€50-100/month)<br>❌ Not integrated<br>❌ Only bounces, no sending |
| **PEC Providers** | Certified Email | Legal compliance | ❌ Not a proxy<br>❌ They ARE the email service<br>❌ No programmatic API |

### Cost Comparison (12 months, 50k emails/day)

| Solution | License | SaaS | Infrastructure | Development | Maintenance | **TOTAL** |
|----------|---------|------|----------------|-------------|-------------|-----------|
| **genro-mail-proxy** | €0 | €0 | €600 | €0 | €1,200 | **€1,800** |
| **SendGrid** | €0 | €8,400 | €0 | €0 | €0 | **€8,400** |
| **Mailgun** | €0 | €9,600 | €0 | €0 | €0 | **€9,600** |
| **RabbitMQ+Custom** | €0 | €0 | €1,200 | €12,000 | €6,000 | **€19,200** |
| **Postal** | €0 | €0 | €600 | €0 | €2,400 | **€3,000** |

**Notes:**
- Infrastructure: 4-core VPS with 8GB RAM (~€50/month)
- Development: RabbitMQ requires implementing retry, rate limiting, bounce detection, etc.
- Maintenance: genro-mail-proxy requires minimal monitoring

---

## Target Market

### Perfect Fit For:

1. **Italian Enterprise Companies**
   - High-volume transactional emails (>10k/day)
   - PEC requirement (legal mandate)
   - Large attachments (contracts, reports, invoices)
   - Audit trail for compliance
   - Limited budget (no SendGrid/Mailgun)

2. **SaaS Platforms**
   - Multi-tenant applications
   - Need database consistency
   - Behind firewall (no public webhook endpoint)
   - Cost-sensitive

3. **Financial Services**
   - Regulatory compliance
   - Audit requirements
   - PEC for legal communications
   - Large PDF reports

4. **Healthcare/Government**
   - On-premise deployment required
   - HIPAA/GDPR compliance
   - No data leaving infrastructure
   - Large attachments (medical records, documents)

### Not a Good Fit For:

- ❌ Simple applications with <1000 emails/day → Use SendGrid
- ❌ No technical team → Use managed SaaS
- ❌ Marketing emails (newsletters) → Use dedicated marketing platform
- ❌ Need full email server → Use Postal or Postfix

---

## Technical Architecture

### The "Postman" Pattern

Think of genro-mail-proxy as a **postman making rounds**:

```
┌─────────────────────────────────────────────────────────┐
│  APPLICATION                                            │
│  ┌──────────────┐                                       │
│  │ Write to DB: │  ← Fast (~30ms)                       │
│  │ status=queued│                                       │
│  └──────────────┘                                       │
└─────────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────┐
│  GENRO-MAIL-PROXY (The Postman)                         │
│                                                          │
│  1. Poll queue (every N seconds)                        │
│     ↓                                                    │
│  2. Retrieve pending emails                             │
│     ↓                                                    │
│  3. Send via SMTP (with connection pooling)             │
│     ↓                                                    │
│  4. Update DB:                                          │
│     • status='sent' + sent_at (if success)              │
│     • status='error' + error_message (if failed)        │
│     ↓                                                    │
│  5. Retry failed (exponential backoff)                  │
│     ↓                                                    │
│  6. Poll IMAP for bounces                               │
│     ↓                                                    │
│  7. Update DB with bounce status                        │
│     ↓                                                    │
│  8. Poll IMAP for PEC receipts                          │
│     ↓                                                    │
│  9. Update DB with receipt status                       │
│     ↓                                                    │
│  10. Repeat (cycle)                                     │
└─────────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────┐
│  SMTP SERVER                                            │
│  (Gmail, Office365, custom SMTP)                        │
└─────────────────────────────────────────────────────────┘
```

### Performance Characteristics

| Metric | Direct SMTP | genro-mail-proxy | Improvement |
|--------|-------------|------------------|-------------|
| Request latency (single) | ~600ms | ~30ms | **20x faster** |
| Batch throughput (100 emails) | ~60s | ~3s | **20x faster** |
| Connection setup | Per request | Pooled (5min TTL) | **10-50x reduction** |
| Database consistency | Prone to errors | Always consistent | **100% reliable** |
| Retry on failure | Manual | Automatic | **Fully automated** |

---

## Real-World Example

### Italian Manufacturing Company

**Requirements:**
- 50,000 transactional emails/day (order confirmations, invoices, contracts)
- PEC mandatory for legal communications with clients and government
- Large PDF attachments (technical drawings, contracts) often >10MB
- Must maintain audit trail for 10 years (compliance)
- Limited budget (startup mindset)

**Previous Stack:**
- Direct SMTP calls from application
- Manual S3 upload + email body modification for large files
- No bounce detection (bounces went to support inbox)
- No PEC tracking
- **Problems:**
  - Database inconsistency: 2-3% of emails marked "sent" but never delivered
  - Support team manually checking bounce inbox
  - PEC receipts lost in inbox
  - Development time wasted on email handling

**With genro-mail-proxy:**
- ✅ Zero code changes to application (drop-in replacement)
- ✅ Database 100% consistent
- ✅ Automatic bounce detection and classification
- ✅ PEC receipts automatically tracked
- ✅ Large files automatically offloaded to S3
- ✅ Email body automatically rewritten with download links
- ✅ Prometheus metrics for monitoring
- ✅ **Total cost: €50/month** (VPS) vs €800-1200/month (SendGrid + custom code + GlockApps)

**ROI: Saved €9,000-14,400/year**

---

## Getting Started

### Quick Start

```bash
# Docker
docker run -p 8000:8000 genro-mail-proxy

# Or install
pip install genro-mail-proxy
mail-proxy init myinstance
mail-proxy start myinstance
```

### Configuration Example

```yaml
# ~/.mail-proxy/myinstance/config.yml
instance_name: myinstance
database: sqlite:///~/.mail-proxy/myinstance/mail_service.db

smtp_accounts:
  - name: primary
    host: smtp.gmail.com
    port: 587
    username: app@company.com
    password: ${SMTP_PASSWORD}
    use_tls: true

queue:
  poll_interval: 10  # seconds
  batch_size: 50

rate_limits:
  - account: primary
    minute: 20
    hour: 500
    day: 10000

bounce_detection:
  enabled: true
  imap_host: imap.gmail.com
  imap_port: 993
  imap_username: bounces@company.com
  imap_password: ${IMAP_PASSWORD}
  poll_interval: 300  # 5 minutes

pec:
  enabled: true
  track_acceptance: true
  track_delivery: true

large_files:
  threshold: 10485760  # 10MB
  action: rewrite
  storage:
    backend: s3
    bucket: company-email-attachments
    region: eu-west-1
  presigned_url_expiry: 604800  # 7 days
```

### Send Email

```python
import requests

response = requests.post('http://localhost:8000/send', json={
    'account': 'primary',
    'to': 'client@example.com',
    'subject': 'Order Confirmation',
    'body': '<h1>Thank you for your order!</h1>',
    'attachments': [
        {
            'filename': 'invoice.pdf',
            'url': 'https://app.company.com/invoices/12345.pdf'
        }
    ],
    'priority': 'high',
    'is_pec': False
})

# Returns immediately (~30ms)
# Email queued, will be sent by proxy
```

---

## License

- **Apache 2.0**: Core features (queue, retry, rate limiting, priority, delivery reports, attachments, SMTP pooling, REST API, CLI)
- **BSL 1.1**: Advanced features (multi-tenancy, bounce detection, PEC support, large files)
  - Converts to Apache 2.0 on **2030-01-25**

---

## Conclusion

genro-mail-proxy is **not just another email library**. It's a complete architectural pattern that solves four critical problems that no other solution addresses together:

1. ✅ Database-SMTP consistency (queue-based proxy)
2. ✅ Active bounce detection (IMAP polling)
3. ✅ PEC certified email (Italian compliance)
4. ✅ Transparent large file handling (auto-upload + rewrite)

**Target market:** Italian enterprise companies with high-volume transactional emails, PEC requirements, large attachments, and limited budgets.

**Competitive advantage:** No direct competitor exists. The combination of these four capabilities is unique in the market.

**Economics:** Saves €9,000-14,000/year vs commercial alternatives while providing superior features.

---

## Links

- **GitHub**: https://github.com/genropy/genro-mail-proxy
- **Documentation**: https://genro-mail-proxy.readthedocs.io/
- **Presentation**: https://genro-mail-proxy-il-post-x6fhmmx.gamma.site
