import urllib.request, json, sys

url = "http://localhost:8000/v3/chat/completions"
data = {
    "model": "qwen2.5-0.5b-instruct",
    "messages": [{"role": "user", "content": "say hi"}],
    "max_tokens": 5,
    "stream": False
}
print("Sending request to OVMS...", flush=True)
req = urllib.request.Request(url, json.dumps(data).encode('utf-8'))
req.add_header('Content-Type', 'application/json')
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read().decode('utf-8')
        print("STATUS:", r.status)
        print("RESPONSE:", body[:500])
except urllib.error.HTTPError as e:
    print("HTTP ERROR:", e.code, e.read().decode('utf-8')[:500])
except Exception as e:
    print("ERROR:", type(e).__name__, str(e))
