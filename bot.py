import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from compose import compose
from reply import handle_reply, has_any, AUTO_REPLY_PATTERNS

app = FastAPI()
START = time.time()

# (scope, context_id) -> {"version": int, "payload": dict}
contexts = {}
sent_suppression_keys = set()   # never send the same trigger twice
conversations = {}              # conversation_id -> {"merchant_id", "customer_id", "trigger_id", "turns": [...]}
auto_reply_counts = {}          # merchant_id -> number of auto-replies received


def get_payload(scope, cid):
    item = contexts.get((scope, cid))
    return item["payload"] if item else None


# ---------- Health + metadata ----------

@app.get("/v1/healthz")
def healthz():
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _cid) in contexts:
        counts[scope] = counts.get(scope, 0) + 1
    return {"status": "ok",
            "uptime_seconds": int(time.time() - START),
            "contexts_loaded": counts}


@app.get("/v1/metadata")
def metadata():
    return {"team_name": "Jatin Maggo",
            "team_members": ["Jatin Maggo"],
            "model": "openai/gpt-oss-120b",
            "approach": "4-context LLM composer + rule-based reply router",
            "contact_email": "jatinmaggo28@gmail.com",
            "version": "1.0.0",
            "submitted_at": datetime.now(timezone.utc).isoformat()}


# ---------- Context push ----------

class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict
    delivered_at: Optional[str] = None


@app.post("/v1/context")
def push_context(body: CtxBody):
    if body.scope not in ("category", "merchant", "customer", "trigger"):
        return {"accepted": False, "reason": "invalid_scope", "details": body.scope}
    key = (body.scope, body.context_id)
    cur = contexts.get(key)
    if cur and cur["version"] >= body.version:
        return {"accepted": False, "reason": "stale_version",
                "current_version": cur["version"]}
    contexts[key] = {"version": body.version, "payload": body.payload}
    return {"accepted": True,
            "ack_id": f"ack_{body.context_id}_v{body.version}",
            "stored_at": datetime.now(timezone.utc).isoformat()}


# ---------- Tick: proactively send messages ----------

class TickBody(BaseModel):
    now: str
    available_triggers: list = []


@app.post("/v1/tick")
def tick(body: TickBody):
    # 1. Keep only valid triggers
    jobs = []
    for trg_id in body.available_triggers:
        trg = get_payload("trigger", trg_id)
        if not trg:
            continue
        # skip triggers we already sent (spam = penalty)
        skey = trg.get("suppression_key") or trg_id
        if skey in sent_suppression_keys:
            continue
        merchant_id = trg.get("merchant_id") or (trg.get("payload") or {}).get("merchant_id")
        merchant = get_payload("merchant", merchant_id)
        if not merchant:
            continue
        category = get_payload("category", merchant.get("category_slug"))
        if not category:
            continue
        customer_id = trg.get("customer_id")
        customer = get_payload("customer", customer_id) if customer_id else None
        if customer_id and not customer:
            continue  # customer trigger but no customer data yet
        jobs.append((trg_id, trg, merchant_id, merchant, category, customer_id, customer, skey))

    # 2. Most urgent first, max 10 per tick
    jobs.sort(key=lambda j: -(j[1].get("urgency") or 0))
    jobs = jobs[:10]

    # 3. Run all LLM calls in parallel (the judge expects an answer within 15s)
    actions = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(compose, j[4], j[3], j[1], j[6]): j for j in jobs}
        done, _ = wait(futures, timeout=13)
        for fut in done:
            trg_id, trg, merchant_id, merchant, category, customer_id, customer, skey = futures[fut]
            try:
                msg = fut.result()
            except Exception as e:
                print("compose error:", e)
                continue
            conv_id = f"conv_{merchant_id}_{trg_id}"
            conversations[conv_id] = {
                "merchant_id": merchant_id, "customer_id": customer_id,
                "trigger_id": trg_id,
                "turns": [{"from": "bot", "body": msg["body"]}],
            }
            sent_suppression_keys.add(skey)
            actions.append({
                "conversation_id": conv_id,
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                "send_as": msg["send_as"],
                "trigger_id": trg_id,
                "template_name": msg["template_name"],
                "template_params": msg["template_params"],
                "body": msg["body"],
                "cta": msg["cta"],
                "suppression_key": skey,
                "rationale": msg["rationale"],
            })
    return {"actions": actions}


# ---------- Reply: handle merchant/customer responses ----------

class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: Optional[str] = None
    turn_number: int = 1


@app.post("/v1/reply")
def reply(body: ReplyBody):
    # Find the conversation; create a new one if missing (must never crash)
    conv = conversations.get(body.conversation_id)
    if conv is None:
        conv = {"merchant_id": body.merchant_id, "customer_id": body.customer_id,
                "trigger_id": None, "turns": []}
        conversations[body.conversation_id] = conv

    merchant = get_payload("merchant", body.merchant_id) if body.merchant_id else None
    trigger = get_payload("trigger", conv.get("trigger_id")) if conv.get("trigger_id") else None

    # Count auto-replies per merchant (the judge sends a new conversation_id each time)
    key = body.merchant_id or body.conversation_id
    if has_any(body.message, AUTO_REPLY_PATTERNS):
        auto_reply_counts[key] = auto_reply_counts.get(key, 0) + 1

    conv["turns"].append({"from": body.from_role, "body": body.message})

    try:
        result = handle_reply(conv, merchant, trigger, body.message,
                              auto_reply_counts.get(key, 0))
    except Exception as e:
        print("reply error:", e)
        result = {"action": "wait", "wait_seconds": 600,
                  "rationale": "Internal error; backing off."}

    if result.get("action") == "send":
        conv["turns"].append({"from": "bot", "body": result["body"]})
    return result


# ---------- Teardown: wipe state at the end of the test ----------

@app.post("/v1/teardown")
def teardown():
    contexts.clear()
    sent_suppression_keys.clear()
    conversations.clear()
    auto_reply_counts.clear()
    return {"ok": True}