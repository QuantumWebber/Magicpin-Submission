# Vera Bot — magicpin AI Challenge

**Author:** Jatin Maggo · jatinmaggo28@gmail.com
**Live URL:** https://YOUR-URL.onrender.com
**Model:** openai/gpt-oss-120b via Groq (backup: qwen/qwen3.8-27b), temperature 0

## Approach
- **Composer (`compose.py`):** builds a compact context from the 4 layers (category, merchant,
  trigger, customer), resolves the trigger's digest item, and sends it to the LLM with a strict
  system prompt: only facts from context, one CTA in the last sentence, category voice,
  language matching (Hinglish when the merchant/customer prefers it).
- **Per-trigger guidance:** a hint for each of 25+ trigger kinds (research digest, perf dip,
  recall due, competitor opened, etc.) so the "why now" is framed correctly.
- **Validation + one retry:** code checks for multiple CTAs, taboo words, missing salutation,
  internal data leaked to customers, and length; problems are sent back to the LLM once.
- **Customer-facing safety:** merchant stats (views, CTR, peers) are removed from the context
  entirely for customer messages, so they cannot leak.
- **Reply router (`reply.py`):** rule-based detection first (stop/hostile → end, auto-reply →
  one probe then end, tracked per merchant, busy → wait, yes/intent → action mode with no
  qualifying questions), then the LLM for answers and off-topic redirection.
- **Reliability:** parallel composition in `/v1/tick` (~4s for 10 triggers), retries + backup
  model on rate limits, deterministic fallback so the bot never returns an empty body,
  suppression keys to avoid repeat sends.

## Tradeoffs
- Rules before LLM for reply routing: faster and predictable on the replay tests, but may miss
  unusual phrasings.
- Free-tier LLM keeps cost at zero but adds rate-limit risk, mitigated with retries and a backup model.
- In-memory state: simple and fast, but lost on restart.

## What would help most
- Real appointment times/services in trigger payloads (several were placeholders).
- Historical reply rates per trigger kind, to learn which framings merchants actually respond to.
- A per-merchant "do not contact" window and preferred contact hours.