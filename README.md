# Wapio WhatsApp for Odoo 19

Free LGPL-3 addon for contacts, CRM leads, quotations and customer invoices. Requires an existing Wapio account, API key and separately purchased service plan. Odoo hosting/subscriptions are separate.

Copy `wapio_whatsapp` to your addons directory and install **Wapio WhatsApp**. It depends on CRM, Sales and Accounting. Odoo 19 self-hosted Community is installation-tested; Enterprise/Odoo.sh-specific deployment is not independently tested. Odoo Online is unsupported. Odoo 18 is not supported by this branch.

As administrator, open Wapio → Connections, enter the company API key and refresh sessions. Grant Wapio Sender to authorized staff. Open a source form and choose Actions → Send WhatsApp with Wapio. Confirm the recipient expects the message and queue it. The minute-based scheduler submits queued messages, with a separate upload stage for PDFs.

Submitted means Wapio accepted the request, not confirmed WhatsApp delivery. Retry an existing failed queue record to preserve its message ID and payload. A new composer creates a new intended message. No incoming-message synchronization or automatic business-event notifications are included.

Recipient, text and selected PDF are transmitted to Wapio after sender opt-in. Credentials and queue data reside in your Odoo database; restrict database/backup access. Keys are administrator-only and UI-masked, not encrypted by this addon. PDFs use Wapio media storage; no public Odoo attachment controller is added. Existing webhook destinations are untouched.

Ten Odoo TransactionCase tests passed on Odoo 19/Python 3.12/PostgreSQL 14 with HTTP mocked at the network boundary. Real quotation and invoice PDFs were separately rendered and staged with the official wkhtmltopdf renderer. Screenshots use synthetic local records. Live Wapio sends and store listing are not yet verified. No Docker was used.

Run tests on a disposable database:

```sh
odoo-bin -d wapio_connector_test --addons-path=/path/to/odoo/addons,/path/to/this/repository \
  -i wapio_whatsapp --without-demo --test-enable --test-tags=/wapio_whatsapp --stop-after-init
```

API documentation: https://www.wapio.io/docs. Support: contact@wapio.io.
