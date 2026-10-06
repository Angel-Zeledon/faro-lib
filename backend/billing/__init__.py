"""Online payments for the Full plan: Stripe and PayPal, hosted pages only.

No card data ever reaches this server or the app's JavaScript: a checkout is a
redirect to the provider's own page, and the only thing that ever changes a
tenant's tier is a webhook whose signature this module verified.

    entitlement.py  the ONE pure function: subscription state -> tier
    signatures.py   Stripe-Signature verification (stdlib hmac)
    providers.py    which providers are configured, from the registry
    stripe_api.py   the few Stripe calls, over httpx
    paypal_api.py   the few PayPal calls, over httpx
    service.py      persistence: customers, subscriptions, events, the tier
    webhooks.py     event -> provider subscription -> service
"""
