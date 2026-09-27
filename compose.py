import json
import re
from llm import call_llm

SYSTEM_PROMPT = """You are Vera, magicpin's WhatsApp assistant for Indian local merchants.
You write ONE WhatsApp message using ONLY the facts in the CONTEXT JSON.

HARD RULES:
1. NEVER invent numbers, offers, prices, research, competitor names, dates or slots.
   Use only what is in CONTEXT. If a fact is missing, write around it.
2. Anchor on at least one concrete, verifiable fact (a number, %, date, source, peer stat).
3. Say clearly WHY NOW: open by naming the trigger (the event that caused this message).
4. Offers must be service+price from CONTEXT (e.g. "Dental Cleaning @ ₹299"), never generic "X% off".
5. Exactly ONE call-to-action, in the LAST sentence. Prefer a binary "Reply YES" style.
   Pure information messages may have no CTA.
6. Tone must follow category voice. Clinical categories (dentists, pharmacies) = peer/colleague,
   no hype, no taboo words. Never use words listed in voice.vocab_taboo.
7. Language: if language_hint says hinglish, write natural Hindi-English code-mix in Roman script.
   Otherwise English.
8. No long greeting or preamble, no "I hope you are doing well". Do not re-introduce yourself
   if conversation_history shows earlier Vera messages.
9. 2-5 short sentences. Use effort externalization when possible ("I've drafted X, just say YES").
10. If recent conversation shows the merchant asked for something, connect to it.
11. (Merchant-facing only) ALWAYS open with the merchant's salutation. Use
    category.voice.salutation_examples with owner_first_name (e.g. "Dr. Meera," for dentists,
    or first name for others).
12. When citing a research/regulation/news fact, name its source from trigger_digest_item.source
    (e.g. "DCI circular", "JIDA Oct 2026 p.14") so the merchant can verify it.
13. (Merchant-facing only) Add one engagement lever: a deadline from the trigger payload, loss
    aversion ("X patients at risk", "missed searches"), or social proof from peer_stats.
14. Follow CONTEXT.trigger_hint for how to frame this specific trigger.
15. Only offer promos from merchant.active_offers. offer_catalog is only a style reference for
    suggestions to the merchant; NEVER present a catalog item to a customer as if the merchant runs it.
16. Do not invent plan details (quantities, add-ons, delivery slots, discounts) that are not in
    CONTEXT. If proposing a plan, build it only from CONTEXT facts and offer to "draft" the rest.
    If you must suggest a new number (e.g. min order), phrase it as an editable suggestion
    ("say 30 covers, adjust as you like"), never as a fact.

CUSTOMER-FACING MODE (when CONTEXT.customer is present):
- Follow CONTEXT.recipient: write as the merchant's business, addressed to the customer by first name.
- Mention the reason (recall due, appointment, etc.) and real slots/offers from CONTEXT.
- A numbered slot choice ("Reply 1 for Wed, 2 for Thu") is allowed here.
- No medical claims or guarantees.
- NEVER mention the merchant's internal data to the customer: no views, CTR, calls, peer stats,
  signals, subscription or revenue. The customer only cares about their own visit.
- Keep it to 2-3 warm, short sentences. Engagement comes from convenience (slot, reminder), not stats.

Return ONLY JSON with keys:
{
  "body": "<the WhatsApp message>",
  "cta": "binary_yes_stop" | "open_ended" | "multi_choice_slot" | "none",
  "template_name": "<short snake_case name like vera_research_digest_v1>",
  "template_params": ["<value1>", "<value2>", "<value3>"],
  "rationale": "<1-2 sentences: which trigger, which facts used, which engagement lever>"
}"""


TRIGGER_HINTS = {
    "research_digest": "Cite source + trial size + key %; link it to this merchant's patient/customer cohort; offer to draft patient-education content.",
    "regulation_change": "State the rule, the source and the deadline; frame as compliance risk (loss aversion); offer a quick checklist/audit.",
    "recall_due": "Customer-facing: name the service due and time since last visit; offer the real slots from payload; numbered slot choice allowed.",
    "appointment_tomorrow": "Customer-facing: short friendly reminder; include time/service only if present in payload; confirm or reschedule CTA.",
    "chronic_refill_due": "Customer-facing: remind which refill is due using payload; offer home delivery/pickup only if in context; no medical advice.",
    "customer_lapsed_soft": "Customer-facing: warm check-in addressed to the customer; mention last visit date; offer to book; use a real merchant offer only if one exists.",
    "customer_lapsed_hard": "Customer-facing: warm win-back; mention last service/visit; use a real merchant offer only if one exists.",
    "winback_eligible": "Suggest a win-back campaign to lapsed customers using lapsed counts from customer_aggregate; offer to draft it.",
    "perf_dip": "Name the exact metric and % drop; give one likely reason from signals; offer one concrete fix you can do now.",
    "seasonal_perf_dip": "Say the dip is seasonal (cite seasonal_beats) so they don't panic; suggest one counter-move.",
    "perf_spike": "Celebrate with the exact number; suggest capitalising now (post / offer) before the spike fades.",
    "milestone_reached": "Congratulate with the exact milestone; suggest one way to use it (post / review ask).",
    "competitor_opened": "Mention distance/details only from payload; never invent competitor names; suggest a defensive move.",
    "festival_upcoming": "Name the festival and days left; propose a service+price offer from their catalog; offer to draft the post.",
    "ipl_match_today": "Tie to today's match timing; propose a match-time offer from their catalog; offer to draft it.",
    "category_seasonal": "Use the seasonal beat from category; connect to one of their services; offer a ready draft.",
    "review_theme_emerged": "Quote the theme and count from review_themes; suggest one fix plus a reply template.",
    "dormant_with_vera": "Re-open with curiosity: one surprising fact from their data; ask one easy question.",
    "curious_ask_due": "Ask the merchant one easy, specific question about their business (e.g. most-asked service this week).",
    "gbp_unverified": "Explain what unverified costs them (visibility) with a number if available; offer to guide verification.",
    "renewal_due": "State days remaining and what they'd lose (their own numbers); single renew CTA; no pressure tone.",
    "trial_followup": "Reference what they tried in the trial and one result number; ask to continue.",
    "supply_alert": "State the supply issue from payload; suggest how to inform customers or substitute.",
    "active_planning_intent": "Merchant already showed intent; skip pitching, move to a concrete draft plan with their numbers.",
    "wedding_package_followup": "Follow up on the wedding package using payload details; offer a ready draft package.",
    "cde_opportunity": "Mention the CDE/training opportunity from payload with date/credits; low-pressure interest CTA.",
}


def find_digest_item(category: dict, trigger: dict):
    """Find the category digest item that the trigger's top_item_id points to."""
    item_id = (trigger.get("payload") or {}).get("top_item_id")
    if not item_id:
        return None
    for item in category.get("digest", []):
        if item.get("id") == item_id:
            return item
    return None


def language_hint(merchant: dict, customer: dict | None) -> str:
    """Pick the message language from the customer's or merchant's language settings."""
    if customer:
        pref = str((customer.get("identity") or {}).get("language_pref", "")).lower()
        return "hinglish" if ("hi" in pref or "mix" in pref) else "english"
    langs = (merchant.get("identity") or {}).get("languages", [])
    return "hinglish" if "hi" in langs else "english"


def build_context(category, merchant, trigger, customer):
    """Send the LLM only the useful data (smaller prompt = faster + fewer hallucinations)."""
    offers = merchant.get("offers", [])
    ctx = {
        "trigger": {
            "kind": trigger.get("kind"),
            "source": trigger.get("source"),
            "urgency": trigger.get("urgency"),
            "payload": trigger.get("payload"),
        },
        "trigger_digest_item": find_digest_item(category, trigger),
        "merchant": {
            "name": merchant.get("identity", {}).get("name"),
            "owner_first_name": merchant.get("identity", {}).get("owner_first_name"),
            "locality": merchant.get("identity", {}).get("locality"),
            "city": merchant.get("identity", {}).get("city"),
            "subscription": merchant.get("subscription"),
            "performance_30d": merchant.get("performance"),
            "active_offers": [o["title"] for o in offers if o.get("status") == "active"],
            "expired_offers": [o["title"] for o in offers if o.get("status") != "active"],
            "customer_aggregate": merchant.get("customer_aggregate"),
            "signals": merchant.get("signals"),
            "review_themes": merchant.get("review_themes"),
            "recent_conversation": (merchant.get("conversation_history") or [])[-4:],
        },
        "category": {
            "slug": category.get("slug"),
            "voice": category.get("voice"),
            "peer_stats": category.get("peer_stats"),
            "offer_catalog": [o.get("title") for o in category.get("offer_catalog", [])],
            "seasonal_beats": category.get("seasonal_beats"),
            "trend_signals": category.get("trend_signals"),
        },
        "language_hint": language_hint(merchant, customer),
        "trigger_hint": TRIGGER_HINTS.get(
            trigger.get("kind"),
            "Explain clearly why this matters now, using their own numbers."),
    }

    if customer:
        ctx["customer"] = {
            "name": customer.get("identity", {}).get("name"),
            "state": customer.get("state"),
            "relationship": customer.get("relationship"),
            "preferences": customer.get("preferences"),
            "consent_scope": (customer.get("consent") or {}).get("scope"),
        }
        ctx["recipient"] = (f"The CUSTOMER {ctx['customer']['name']}. Address them by name. "
                            f"Do NOT address the merchant/owner.")
        # The customer must never see the merchant's internal data, so don't send it to the LLM
        for k in ["performance_30d", "customer_aggregate", "signals", "review_themes",
                  "subscription", "recent_conversation", "expired_offers"]:
            ctx["merchant"].pop(k, None)
        for k in ["peer_stats", "trend_signals", "seasonal_beats"]:
            ctx["category"].pop(k, None)

    return ctx


def validate(body: str, category: dict, merchant: dict, customer: dict | None) -> list:
    """Find problems in the message. Empty list = everything is fine."""
    problems = []
    low = body.lower()

    # 1. More than one CTA
    cta_hits = len(re.findall(r"\b(reply|say|type|send)\s+(yes|confirm|1|2|stop)\b", low))
    if cta_hits > 1:
        problems.append("There are multiple CTAs. Keep exactly ONE call-to-action, in the last sentence.")

    # 2. Taboo words
    for w in (category.get("voice") or {}).get("vocab_taboo", []):
        word = w.split("(")[0].strip().lower()
        if word and word in low:
            problems.append(f"Remove the taboo word/phrase '{word}'.")

    # 3. Missing name
    if customer:
        cname = (customer.get("identity") or {}).get("name") or ""
        if cname and cname.lower() not in low[:40]:
            problems.append(f"Address the CUSTOMER by name ({cname}) at the start, not the merchant.")
    else:
        owner = (merchant.get("identity") or {}).get("owner_first_name") or ""
        if owner and owner.lower() not in low[:60]:
            problems.append(f"Start the message by addressing the merchant as per salutation (name: {owner}).")

    # 4. Internal data leaked to a customer
    if customer and re.search(r"\b(ctr|views|peer|peers|calls)\b", low):
        problems.append("Customer message must not mention views, CTR, calls or peers. Remove them.")

    # 5. Too long
    if len(body) > 480:
        problems.append("Too long. Cut to 2-4 short sentences.")

    return problems


def fallback_message(merchant, trigger, customer):
    """If the LLM fails, still send a correct message with no invented facts (English)."""
    ident = merchant.get("identity", {})
    owner = ident.get("owner_first_name") or ident.get("name", "")
    mname = ident.get("name", "our clinic")
    topic = str(trigger.get("kind", "update")).replace("_", " ")

    # Customer-facing
    if customer:
        cname = customer.get("identity", {}).get("name", "")
        body = (f"Hi {cname}, this is {mname}. We have an update about your {topic}. "
                f"Reply YES and we'll share the details.")
        return body, "binary_yes_stop"

    # Merchant-facing: include a real number so it doesn't feel generic
    perf = merchant.get("performance", {})
    views, calls = perf.get("views"), perf.get("calls")
    fact = ""
    if views is not None and calls is not None:
        fact = f" Your profile got {views} views and {calls} calls in the last 30 days."
    elif views is not None:
        fact = f" Your profile got {views} views in the last 30 days."
    body = (f"Hi {owner}, there's a new {topic} alert for your listing.{fact} "
            f"Want me to send the details? Reply YES.")
    return body, "binary_yes_stop"


def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None = None) -> dict:
    ctx = build_context(category, merchant, trigger, customer)
    user_prompt = "CONTEXT:\n" + json.dumps(ctx, ensure_ascii=False, default=str)

    out = call_llm(SYSTEM_PROMPT, user_prompt)

    # Validate; if there are problems, ask the LLM to fix them once
    first_body = (out.get("body") or "").strip()
    if first_body:
        problems = validate(first_body, category, merchant, customer)
        if problems:
            fix_prompt = (user_prompt
                          + "\n\nYOUR PREVIOUS DRAFT:\n" + first_body
                          + "\n\nFIX THESE PROBLEMS and return the corrected JSON:\n- "
                          + "\n- ".join(problems))
            fixed = call_llm(SYSTEM_PROMPT, fix_prompt)
            if (fixed.get("body") or "").strip():
                out = fixed

    body = (out.get("body") or "").strip()
    cta = out.get("cta") or "open_ended"
    rationale = out.get("rationale") or ""
    if not body:  # LLM failed -> use the backup message
        body, cta = fallback_message(merchant, trigger, customer)
        rationale = "Fallback template (LLM unavailable); anchored on trigger kind and merchant views."

    # Customer-scope triggers are sent on the merchant's behalf
    is_customer_msg = bool(customer) or trigger.get("scope") == "customer"

    return {
        "body": body,
        "cta": cta,
        "send_as": "merchant_on_behalf" if is_customer_msg else "vera",
        "suppression_key": trigger.get("suppression_key", trigger.get("id", "")),
        "template_name": out.get("template_name") or f"vera_{trigger.get('kind', 'generic')}_v1",
        "template_params": out.get("template_params") or [],
        "rationale": rationale,
    }