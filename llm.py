import os
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()  # read keys from the .env file

API_KEY = os.getenv("LLM_API_KEY")
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
BACKUP_MODEL = os.getenv("LLM_BACKUP_MODEL", "qwen/qwen3.8-27b")


def _one_call(model, system_prompt, user_prompt, timeout):
    """Make a single LLM request and return the parsed JSON."""
    body = {
        "model": model,
        "temperature": 0,  # rule: same input -> same output
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if "gpt-oss" in model:
        body["reasoning_effort"] = "low"  # keep reasoning short so we stay fast
    resp = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}",
                 "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"{resp.status_code}: {resp.text[:200]}")
    text = resp.json()["choices"][0]["message"]["content"]
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def call_llm(system_prompt: str, user_prompt: str, timeout: int = 12) -> dict:
    """Try the main model twice, then the backup model. If everything fails, return {}.
    Tries to finish within ~13 seconds so the judge's 15s timeout is never hit."""
    start = time.time()
    attempts = [(MODEL, 0), (MODEL, 1.0), (BACKUP_MODEL, 0)]
    for model, wait_before in attempts:
        remaining = 13 - (time.time() - start)
        if remaining < 3:
            break
        if wait_before:
            time.sleep(wait_before)
        try:
            return _one_call(model, system_prompt, user_prompt,
                             timeout=min(timeout, remaining))
        except Exception as e:
            print(f"LLM error ({model}):", e)
    return {}


if __name__ == "__main__":
    # Quick test: python llm.py
    t = time.time()
    out = call_llm(
        "You reply only in JSON.",
        'Return {"greeting": "<a short friendly hello for a dentist>"}'
    )
    print(out)
    print(f"Time: {time.time() - t:.1f}s")