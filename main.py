import json
import requests
import os

message = input("You: ")
print("Thinking...")
memories = []

try:
    with open("memory.jsonl", "r") as file:
        for line in file:
            line = line.strip()
            if line:
                memories.append(json.loads(line))
except FileNotFoundError:
    print("File does not exist!")
    raise SystemExit

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

model_mode = "cloud"

prompt = f"""
You are Ennoic's memory interpreter.

Your task is to interpret the user's message and return exactly one
valid JSON object. Do not answer the user directly.

The JSON object must contain exactly these five fields:
- action
- intent
- person
- object
- time

All five values must be strings. If a field has no value, use "".
Do not add, omit, or rename any fields.

ACTION CLASSIFICATION

Choose exactly one action:

1. "remember"
The user provides information that should be stored in memory,
including new information and corrections to previously stored information.

2. "ask"
The user wants to retrieve information from Ennoic's memory.
A valid question remains "ask" even when the answer may not exist
in the current memories.

3. "forget"
The user explicitly wants information removed from memory.

4. "unknown"
Use this when the message is unintelligible, random gibberish,
or an unsupported request whose intended operation cannot be identified.

Do not classify a message using individual keywords alone.
Determine the user's intended operation from the entire message.
Never invent an intention when the message does not provide enough evidence.

IMPORTANT DISTINCTIONS

- New information and corrections both use "remember".
- Do not use an "update" action.
- A question about remembered information uses "ask".
- A question with no matching memory is still "ask", not "unknown".
- Greetings, random characters, and requests unrelated to the current
  memory operations should use "unknown" unless their intended operation
  is otherwise clear.

THE "intent" FIELD

Use:
- "who" when the user wants to identify a person associated with something.
- "when" when the user wants to know a time, date, or schedule.
- "" when neither retrieval intent applies.

The person's name does not determine the intent.
Determine intent from what the user wants to know.

Examples:

User: Who has the keys?
Output:
{{"action":"ask","intent":"who","person":"","object":"keys","time":""}}

User: When does Alex have the keys?
Output:
{{"action":"ask","intent":"when","person":"Alex","object":"keys","time":""}}

User: Remember that Alex has the keys.
Output:
{{"action":"remember","intent":"","person":"Alex","object":"keys","time":""}}

THE "person" FIELD

Extract the name of the person the information concerns.
Use "" when no person can be identified.
Do not invent names.

THE "object" FIELD

Extract the main thing, item, or subject the message concerns.
Use a short, clear description.
Use "" when no object can be identified.

THE "time" FIELD

Extract the time, date, or schedule explicitly provided by the user.
Preserve its meaning and use a concise representation.
Use "" when no time is provided.
For retrieval questions, do not invent an answer time.

EXAMPLES

User: Jamie is bringing the laptop tomorrow.
Output:
{{"action":"remember","intent":"","person":"Jamie","object":"laptop","time":"tomorrow"}}

User: Jamie is bringing the laptop next Friday instead.
Output:
{{"action":"remember","intent":"","person":"Jamie","object":"laptop","time":"next Friday"}}

User: Who has the camera?
Output:
{{"action":"ask","intent":"who","person":"","object":"camera","time":""}}

User: When will Morgan send the documents?
Output:
{{"action":"ask","intent":"when","person":"Morgan","object":"documents","time":""}}

User: Forget the headphones.
Output:
{{"action":"forget","intent":"","person":"","object":"headphones","time":""}}

User: Forget Alex's keys.
Output:
{{"action":"forget","intent":"","person":"Alex","object":"keys","time":""}}

User: Who has the keys?
Output:
{{"action":"ask","intent":"who","person":"","object":"keys","time":""}}

User: sdlkfhsdlfkhsklfh
Output:
{{"action":"unknown","intent":"","person":"","object":"","time":""}}

User: Hello
Output:
{{"action":"unknown","intent":"","person":"","object":"","time":""}}

User: What is 2 + 2?
Output:
{{"action":"unknown","intent":"","person":"","object":"","time":""}}

FINAL RULES

1. Return valid JSON only.
2. Do not use Markdown or code fences in your response.
3. Do not explain the classification.
4. Do not include text before or after the JSON object.
5. Do not guess missing names, objects, or times.
6. Use only the four permitted actions: remember, ask, forget, unknown.
7. Always include all five required fields.
8. Start with {{ and end with }}.

Current memories:
{memories}

User message:
{message}
"""

if model_mode == "local":
    ai_response = ask_model(prompt, "qwen2.5:1.5b")
elif model_mode == "cloud":
    ai_response = ask_cloud_model(prompt)

ai_response = ai_response.strip().removeprefix("```json").removesuffix("```").strip()

try:
    ai_data = json.loads(ai_response)
except json.JSONDecodeError:
    print("Model returned invalid JSON!")
    raise SystemExit

mode = ai_data["action"]
time = ai_data["time"]
person = ai_data["person"]
object = ai_data["object"]

if mode == "remember":
    memory = {
        "person": person,
        "object": object,
        "time": time
        }
    
    match = False

    for m in memories:
        if person == m["person"] and object == m["object"]:
            match = True
            m["time"] = time
            print("Updated new info")

    if match:
        with open("memory.jsonl", "w", encoding="utf-8") as file:
            for m in memories:
                file.write(json.dumps(m) + "\n")
    else:
        with open("memory.jsonl", "a", encoding="utf-8") as file:
            file.write(json.dumps(memory) + "\n")
            print("Stored new info")

elif mode == "ask":
    intent = ai_data["intent"]
    target = ai_data["object"]
    answer = ""
    people = []

    for m in memories:
        if m["object"] == target and (person == "" or m["person"] == person):
            if intent == "who":
                people.append(m["person"])
            elif intent == "when":
                answer = m["time"]
                break

    if intent == "who":
        answer = ", ".join(people)

    response = ask_cloud_model(
         f"""Answer the user's question using the retrieved memory.
        Keep it brief and factual.
        You MUST include every person listed in the retrieved memory.
        Do not omit any matching person or invent information.
        Treat retrieved memory as the source of truth.
        For a who question, list every person in the retrieved memory.
        Don't claim the information is missing when matching names are present.
        Never invent names or facts.
        Retrieved memory: {answer}
        Question: {message}"""
    )

    print(response)

elif mode == "forget":
    new_memories = []
    object = ai_data["object"]
    person = ai_data["person"]

    for m in memories:
        if m["object"] != object or m["person"] != person:
            new_memories.append(m)

    with open("memory.jsonl", "w", encoding="utf-8") as file:
        for m in new_memories:
            file.write(json.dumps(m) + "\n")

    print("Memory deleted!")

elif mode == "unknown":
    response = ask_cloud_model(
        f"""The user sent: '{message}'
        That message didn't match any supported memory action.
        Reply briefly saying you can't help with that."""
    )

    print(response)
    
else:
    print("Invalid mode!")