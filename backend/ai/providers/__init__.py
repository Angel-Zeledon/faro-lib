"""A company's own AI provider ("bring your own LLM"), owner's request 2026-10-13.

A company may choose the model its assistant, narratives, analyst and data
diagnosis run on — OpenAI, Claude, Gemini, Groq, OpenRouter, DeepSeek, or any
OpenAI-compatible URL (Ollama, LM Studio, vLLM, Together, Mistral, Azure) — by
filling one form. What does NOT change is the principle in
`backend/ai/local_llm.py`: the provider is an EXPLICIT STORED CHOICE, never
"whichever key exists", and there is NO fallback chain. When the company's
provider fails, each feature degrades to its rule-based text and says which
provider problem happened; it never answers from StockAI's DeepSeek instead.
A company with no stored choice behaves exactly as before.

Modules: `catalog` (the providers), `errors` (one typed failure set + the key
scrubber), `transport` (SSRF-safe pinned HTTP), `adapters` (the two wire
shapes), `store` (the encrypted row, health, breaker, usage), `client` (the
interface every consumer calls, with retries and the breaker), `service` (what
the API and the capabilities read).

Endpoints used (plain httpx, no vendor SDK), checked against each provider's
documentation on 2026-10-13:

  openai      POST https://api.openai.com/v1/chat/completions   GET /v1/models
              (Bearer; `max_completion_tokens`; optional OpenAI-Organization /
              OpenAI-Project headers) — platform.openai.com/docs/api-reference
  anthropic   POST https://api.anthropic.com/v1/messages         GET /v1/models?limit=100
              (x-api-key + anthropic-version: 2023-06-01; system top-level;
              tool_use / tool_result blocks) — docs.claude.com, Messages API
  google      POST https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
              GET  .../v1beta/openai/models (Bearer) — ai.google.dev/gemini-api/docs/openai
  groq        POST https://api.groq.com/openai/v1/chat/completions  GET .../models
              (Bearer) — console.groq.com/docs/openai, /docs/models
  openrouter  POST https://openrouter.ai/api/v1/chat/completions    GET .../models
              (Bearer; `:free` model variants; 402 = no credits, Retry-After on
              429/503) — openrouter.ai/docs/api-reference/overview, /errors
  deepseek    POST https://api.deepseek.com/chat/completions        GET /models (Bearer)
  custom      POST {base}/chat/completions  GET {base}/models (Bearer, plus `api-key`
              for *.openai.azure.com); a query in the base URL is preserved.

Error normalisation (`errors.classify_http`):

  401                               -> invalid_key
  403 (moderation / guardrail text) -> content_blocked, otherwise invalid_key
  402, `insufficient_quota`, billing / credit balance text -> quota_exceeded
  429                               -> rate_limited (Retry-After / retry-after-ms honoured, capped 30 s)
  404                               -> model_not_found
  400/413/422: key text (Gemini's API_KEY_INVALID) -> invalid_key;
               context too long     -> bad_request {reason: context_too_long};
               safety text          -> content_blocked;
               unknown model text   -> model_not_found; otherwise bad_request
  408 / read timeout                -> timeout (phase read)
  connect timeout                   -> timeout (phase connect)
  5xx, 529                          -> provider_down
  3xx (never followed)              -> base_url_refused {reason: redirect}
  DNS / refused / TLS               -> unreachable
  refused address, http to a public host, credentials in the URL -> base_url_refused
  200 with no choices / empty / truncated / not JSON / too large / an error
      inside the body               -> malformed_response (or the body's own class)
  breaker open                      -> circuit_open (no network call at all)

What is retried (and why nothing is billed twice): only failures where the
provider refused the request or never received it — rate_limited, provider_down,
unreachable before sending, and connect timeouts — with jittered exponential
backoff inside one overall deadline. A read timeout, a connection dropped
mid-answer and anything else with phase "read" are never retried, and neither
is invalid_key, quota_exceeded, model_not_found, bad_request or content_blocked.
"""
