# N29 — Security + Prompt-Injection Defense

## Objective

Treat web pages, emails, tool outputs, and retrieved memory as untrusted data rather than authoritative instructions, and prevent those data sources from silently authorizing actions.

## Architecture

```text
trusted user task / plan
          │
          ├──────────────► authoritative context
          │
untrusted web/email/tool/memory data
          │
          ▼
PromptInjectionGuard
          │
          ├── signal detection
          ├── explicit trust label
          └── non-executable data wrapper
          │
          ▼
ContextManager
          │
          ▼
tool/action sink
          │
          └── independent authorization/approval still required
```

## Capability proof

Runnable capability demo:

```bash
python examples/n29_prompt_injection_demo.py
```

Runnable test:

```bash
pytest -q tests/test_prompt_injection_guard.py
```

The tests detect instruction override, secret-exfiltration and control-bypass patterns, wrap untrusted content as data, prevent untrusted content from authorizing actions, and verify that N27 context marks tool results as untrusted.

## Safety model

N29 uses defense in depth rather than a regex-only claim of complete protection. Structural source/sink separation and existing least-privilege/approval boundaries remain the primary safety controls. This follows current guidance from [OpenAI](https://openai.com/index/designing-agents-to-resist-prompt-injection/) and [OWASP](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html) that prompt injection is evolving and filtering alone is insufficient.

## Limitations after N29

Still deferred:

- consequence-aware risk policy for each action (N30);
- idempotent external side effects and transactions (N31);
- adversarial benchmark suite beyond focused deterministic cases (N40).

## Acceptance gate

N29 is VERIFIED when focused injection/trust-boundary tests and full CI are green, untrusted content cannot authorize actions, and N27 context preserves the data/instruction separation.
