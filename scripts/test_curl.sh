#!/bin/bash
curl -s -X POST http://localhost:8000/v3/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "phi-3.5-mini-instruct", "messages": [{"role": "user", "content": "say hi"}], "max_tokens": 10, "stream": false}' \
  --max-time 120
echo ""
