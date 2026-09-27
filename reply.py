import json
import re
from llm import call_llm

# ---------- 1. Detection rules ----------
# These lists contain English AND Hindi/Hinglish phrases on purpose:
# they are what the bot LISTENS FOR in merchant messages, not what it sends.

AUTO_REPLY_PATTERNS = [
    "thank you for contacting", "thanks for contacting", "our team will",
    "will respond shortly", "will get back to you", "we will get back",
    "automated assistant", "auto reply", "auto-reply", "currently unavailable",
    "out of office", "aapki jaankari ke liye", "team tak pahuncha",
    "hamari team", "jaldi hi sampark",
]

STOP_PATTERNS = [
    "stop", "unsubscribe", "spam", "not interested", "don't message", "dont message",
    "do not message", "stop messaging", "leave me alone", "useless", "block",
    "mat bhejo", "band karo", "nahi chahiye", "interest nahi", "pareshan mat",
]

LATER_PATTERNS = [
    "later", "busy", "baad mein", "baad me", "abhi nahi", "kal baat",
    "call you back", "in a meeting", "thodi der",
]

YES_PATTERNS = [
    "yes", "yeah", "yep", "sure", "ok lets", "ok let's", "okay lets", "let's do",
    "lets do", "go ahead", "do it", "proceed", "haan", "han ji", "haanji", "ji haan",
    "kar do", "karo", "chalo", "theek hai", "thik hai", "done", "whats next",
    "what's next", "i want to join", "judna hai", "judrna hai", "send it", "please do",
]


def has_any(text: str, patterns: list) -> bool:
    t = text.lower()
    return any(p in t for p in patterns)


def is_stop(text: str) -> bool:
    t = text.lower().strip()
    # match "stop" as a whole word only (ignore words like "non-stop" inside other words)
    return bool(re.search(r"\bstop\b", t)) or has_any(t, STOP_PATTERNS[1:])


def detect_language(text: str) -> str:
    """Detect whether the merchant wrote in Hinglish or English, so we reply in the same language."""
    hindi_words = ["hai", "nahi", "haan", "kya", "karo", "mujhe", "aap", "hum",
                   "chahiye", "kar", "theek", "baad", "abhi", "bhi", "mein", "ji"]
    words = re.findall(r"[a-zA-Z]+", text.lower())
    hits = sum(1 for w in words if w in hindi_words)
    if re.search(r"[\u0900-\u097F]", text) or hits >= 2:
        return "hinglish"
    return "english"


# ---------- 2. LLM reply ----------

REPLY_SYSTEM = """You are Vera, magicpin's WhatsApp assistant for Indian local merchants.
You are in the MIDDLE of a conversation. Write the next reply.

RULES:
- Use ONLY facts from CONTEXT. Never invent numbers, offers, names or dates.
- Do NOT re-introduce yourself. No greetings/preamble.
- Reply in the language given by reply_language (hinglish = natural Roman Hindi-English mix).
- 1-3 short sentences. One CTA at most, in the last sentence.
- Never repeat any sentence from previous bot messages.

MODE instructions:
- "action": The merchant has AGREED. Do NOT ask any qualifying question.
  Confirm you are doing it now ("Done", "Drafting now", "Sending now"), state what they get
  concretely (using CONTEXT facts), and give one simple next step (e.g. "Reply CONFIRM to publish").
- "answer": Answer the merchant's question helpfully from CONTEXT. If it is off-topic
  (e.g. GST, taxes, personal matters), politely say it is outside what you can help with and
  steer back to the original topic in one line.
- "auto_reply_probe": The message looks like an automated reply. Politely ask if the owner/manager
  can see this, with a one-line reason why it matters. Keep it very short.

Return ONLY JSON: {"body": "...", "cta": "binary_yes_stop|open_ended|none", "rationale": "..."}"""


def llm_reply(mode: str, merchant: dict | None, trigger: dict | None,
              history: list, message: str, language: str) -> dict:
    ctx = {
        "mode": mode,
        "reply_language": language,
        "merchant": {
            "name": (merchant or {}).get("identity", {}).get("name"),
            "owner_first_name": (merchant or {}).get("identity", {}).get("owner_first_name"),
            "performance_30d": (merchant or {}).get("performance"),
            "active_offers": [o.get("title") for o in (merchant or {}).get("offers", [])
                              if o.get("status") == "active"],
            "signals": (merchant or {}).get("signals"),
        },
        "trigger": {"kind": (trigger or {}).get("kind"),
                    "payload": (trigger or {}).get("payload")},
        "conversation_so_far": history[-6:],
        "merchant_latest_message": message,
    }
    return call_llm(REPLY_SYSTEM, "CONTEXT:\n" + json.dumps(ctx, ensure_ascii=False, default=str))


# ---------- 3. Main decision function ----------

def handle_reply(conv: dict, merchant: dict | None, trigger: dict | None,
                 message: str, auto_reply_count: int) -> dict:
    """conv = {"turns": [...]}; auto_reply_count = how many auto-replies this merchant has sent."""
    history = conv.get("turns", [])
    language = detect_language(message)
    prev_bot_bodies = {t["body"] for t in history if t.get("from") == "bot"}

    # A. Hostile / stop -> exit politely
    if is_stop(message):
        return {"action": "end",
                "rationale": "Merchant asked to stop / not interested; exiting gracefully without further messages."}

    # B. Auto-reply
    if has_any(message, AUTO_REPLY_PATTERNS):
        if auto_reply_count >= 2:
            return {"action": "end",
                    "rationale": f"Auto-reply detected {auto_reply_count} times from this merchant; stopping to avoid wasting turns."}
        out = llm_reply("auto_reply_probe", merchant, trigger, history, message, language)
        body = out.get("body") or ("This looks like an automated reply. Could the owner or manager "
                                   "take a quick look? It only takes 2 minutes.")
        return {"action": "send", "body": body, "cta": "open_ended",
                "rationale": "First auto-reply detected; one short probe for the owner before exiting."}

    # C. Later / busy -> wait
    if has_any(message, LATER_PATTERNS) and not has_any(message, YES_PATTERNS):
        return {"action": "wait", "wait_seconds": 1800,
                "rationale": "Merchant is busy / asked for later; backing off 30 minutes."}

    # D. Yes / intent -> action mode, otherwise answer normally
    mode = "action" if has_any(message, YES_PATTERNS) else "answer"
    out = llm_reply(mode, merchant, trigger, history, message, language)
    body = (out.get("body") or "").strip()

    if not body:  # LLM failed -> fallback
        if mode == "action":
            body = ("Done, I'm drafting it now and will send it over. Review it and reply "
                    "CONFIRM, and I'll publish it.")
        else:
            body = "Got it. I'll check the details and get back to you shortly."

    if body in prev_bot_bodies:  # anti-repetition (avoids the -2 penalty)
        body = body + " (Update: the next step is ready.)"

    return {"action": "send", "body": body, "cta": out.get("cta") or "open_ended",
            "rationale": out.get("rationale") or f"Mode={mode}; responded to merchant's latest message."}