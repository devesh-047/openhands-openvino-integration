import urllib.request
import json
import ssl

url = "http://localhost:8000/v3/chat/completions"
data = {
    "model": "phi-3.5-mini-instruct",
    "messages": [{"role": "user", "content": "say hi"}],
    "max_tokens": 10,
    "stream": False
}

req = urllib.request.Request(url, json.dumps(data).encode('utf-8'))
req.add_header('Content-Type', 'application/json')

try:
    with urllib.request.urlopen(req, timeout=120) as r:
        print("STATUS:", r.status)
        print("RESPONSE:", r.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print("HTTP ERROR:", e.code)
    print("BODY:", e.read().decode('utf-8'))
except Exception as e:
    print("ERROR:", str(e))
