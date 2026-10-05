"""Sales by e-mail: a small business forwards its sales report to a private
address of its account and the attachment is ingested like an upload.

Layout: `parse.py` turns a provider's payload into one `ParsedEmail`;
`service.py` owns the per-tenant address, the allow-list and the message log;
`ingest.py` routes a message and runs each attachment through the same
pipeline as `POST /datasets`. The HTTP edge is `backend/api/v1/inbound_email.py`.
"""
