# OpenRouter free runtime

The optional runtime adapter uses only the `openrouter/free` model identifier. It requires `OPENROUTER_API_KEY` and never falls back to paid models. HTTP 429 and other failures stop the request without a paid retry.

Runtime planning is bounded to eight returned steps and is read-only/test oriented. It is not wired into automatic task execution by default.
