# alexa-talk-pal — Architecture Decision Records

Decisions for the alexa-talk-pal component, following the same lightweight
ADR format used elsewhere. See `../architecture.md` for the full plan these
decisions were extracted from.

| # | Title | Status |
|---|---|---|
| [0001](0001-no-local-llm-inference-on-netbook.md) | No local LLM inference on the netbook | accepted |
| [0002](0002-openrouter-cloud-inference-over-local-ollama.md) | OpenRouter cloud inference over local Ollama-on-PC | accepted |
| [0003](0003-self-hosted-https-relay-vs-lambda-arn-endpoint.md) | Self-hosted HTTPS relay vs. AWS Lambda ARN endpoint | accepted |
| [0004](0004-ngrok-free-tier-for-public-ingress.md) | ngrok free tier (persistent domain) for public ingress | accepted |
| [0005](0005-single-catch-all-intent-with-searchquery-slot.md) | Single catch-all intent with an AMAZON.SearchQuery slot | accepted |
| [0006](0006-unpublished-development-mode-skill.md) | Skill stays unpublished / development mode | accepted |
| [0007](0007-cryptography-via-apt-not-pip-build.md) | `cryptography` via apt, not a pip build | accepted |
| [0008](0008-dotenv-in-home-over-etc-environmentfile.md) | Secrets in `.env` (inside `/home`), not `/etc/talkpal/` | accepted |
| [0009](0009-reuse-prior-skill-id-and-invocation-name.md) | Reuse prior skill ID, invocation name, and locale | accepted |
| [0010](0010-opt-in-to-free-endpoint-training.md) | Opt in to OpenRouter free-endpoint logging/training | accepted |
| [0011](0011-lfm2.5-default-model-openrouter-free-fallback.md) | `liquid/lfm-2.5-2.6b:free` default model, `openrouter/free` fallback | accepted |
| [0012](0012-openrouter-stays-primary-local-llama-cpp-not-yet-viable.md) | OpenRouter stays primary; local llama.cpp not yet viable (reasoning-mode truncation) | accepted |
| [0013](0013-certifi-backed-root-of-trust-for-signature-verification.md) | certifi-backed root-of-trust hop for signature verification | accepted |

## Template

New ADRs should follow `template.md`-style structure: Status, Date,
Deciders, Context, Decision, Consequences (Positive / Negative / Follow-ups),
Alternatives considered.
