import json
import time
from compose import compose


def load(path):
    return json.load(open(path, encoding="utf-8"))


pairs = load("expanded/test_pairs.json")["pairs"]

with open("submission.jsonl", "w", encoding="utf-8") as f:
    for p in pairs:
        trigger = load(f"expanded/triggers/{p['trigger_id']}.json")
        merchant = load(f"expanded/merchants/{p['merchant_id']}.json")
        category = load(f"expanded/categories/{merchant['category_slug']}.json")
        customer = (load(f"expanded/customers/{p['customer_id']}.json")
                    if p.get("customer_id") else None)

        out = compose(category, merchant, trigger, customer)

        # If the LLM was rate-limited and we got a fallback, wait and retry (max 3 times)
        tries = 0
        while out["rationale"].startswith("Fallback") and tries < 3:
            print(f"  {p['test_id']} got a fallback, waiting 15s and retrying...")
            time.sleep(15)
            out = compose(category, merchant, trigger, customer)
            tries += 1

        row = {
            "test_id": p["test_id"],
            "body": out["body"],
            "cta": out["cta"],
            "send_as": out["send_as"],
            "suppression_key": out["suppression_key"],
            "rationale": out["rationale"],
        }
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{p['test_id']} done | {trigger['kind']}")
        time.sleep(4)  # stay under the Groq free-tier rate limit

print("\nsubmission.jsonl ready!")