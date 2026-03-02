import requests

url = "http://localhost:8000/v3/chat/completions"
headers = {"Content-Type": "application/json"}
data = {
    "model": "tiny-llama-1.1b-chat",
    "messages": [{"role": "user", "content": "What is OpenVINO?"}],
    "stream": False
}

response = requests.post(url, headers=headers, json=data)
print("Status Code:", response.status_code)
print("Response:", response.text)
