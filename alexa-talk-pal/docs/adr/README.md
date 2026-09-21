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

## Template

New ADRs should follow `template.md`-style structure: Status, Date,
Deciders, Context, Decision, Consequences (Positive / Negative / Follow-ups),
Alternatives considered.
