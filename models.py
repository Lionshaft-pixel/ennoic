import requests
import os

def ask_model(prompt, model): # dumb ahh 1.5B Local model
    response = requests.post(
    "http://localhost:11434/api/generate",
    json={
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0
        }
    }
)
    
    return response.json()["response"]

def ask_cloud_model(prompt): # Cloud model
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY environment variable is missing")
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json={
            "model": "openai/gpt-oss-120b",
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0
        }
    )
    return response.json()["choices"][0]["message"]["content"]