import json, time
from compose import compose

def load(path):
    return json.load(open(path, encoding="utf-8"))

pairs = load("expanded/test_pairs.json")["pairs"]

for p in pairs[:3]:   # pehle 3 pairs test karo
    trigger = load(f"expanded/triggers/{p['trigger_id']}.json")
    merchant = load(f"expanded/merchants/{p['merchant_id']}.json")
    category = load(f"expanded/categories/{merchant['category_slug']}.json")
    customer = load(f"expanded/customers/{p['customer_id']}.json") if p.get("customer_id") else None

    t = time.time()
    out = compose(category, merchant, trigger, customer)
    print(f"\n===== {p['test_id']} | {trigger['kind']} | {time.time()-t:.1f}s =====")
    print(out["body"])
    print("CTA:", out["cta"], "| send_as:", out["send_as"])
    print("WHY:", out["rationale"])