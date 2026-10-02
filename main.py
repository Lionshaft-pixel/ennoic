import json
import requests

message = input("You: ")
message = message.lower()
print("Thinking...")
response = requests.post(
    "http://localhost:11434/api/generate",
    json={
        "model": "qwen3:0.6b",
        "prompt": f"""Return ONLY the JSON object.
    Do not explain your answer.
    Do not write sentences.
    Do not use Markdown.
    Do not use code fences.

    The action field describes what Ennoic should do:
    remember = save new information
    ask = retrieve information
    forget = remove information
    update = change existing information

    Fields:
    action, person, object, time

    User message:
    {message}""",
        "stream": False
    }
)

ai_response = response.json()["response"]
ai_data = json.loads(ai_response)
mode = ai_data["action"]
print(mode)

time = ""
person = ""
object = ""
memories = []

try:
    with open("memory.jsonl", "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                memories.append(json.loads(line))
except FileNotFoundError:
        print("Memory file does not exist!")
        exit()
    
time = ai_data["time"]
person = ai_data["person"]
object = ai_data["object"]

memory = {
    "time": time,
    "person": person,
     "object": object
    }
    
for m in memories:
    if m["person"] == person and m["object"] == object:
        m["time"] = time

with open("memory.jsonl", "w", encoding="utf-8") as file:
    for m in memories:
        file.write(json.dumps(m) + "\n")

with open("memory.jsonl", "a", encoding="utf-8") as file:
    file.write(json.dumps(memory) + "\n")

print("Stored new info")

if mode == "ask":
    if "who" in message:
        intent = "who"
    elif "when" in message:
        intent = "when"
    else:
        print("No intent found")
        exit()

    if "keys" in message:
        target = "keys"
    elif "car" in message:
        target = "car"
    elif "ticket" in message:
        target = "ticket"
    else:
        print("No target found for object")
        exit()

    found = False

    for m in memories:
        if m["object"] == target:
            found = True
            if intent == "who":
                print(m["person"])
            elif intent == "when":
                print(m["time"])
                break

    if not found:
        print("Memory not found!")

elif mode == "forget":
    new_memories = []

    if "keys" in message:
        target = "keys"
    elif "car" in message:
        target = "car"
    elif "ticket" in message:
        target = "ticket"
    else:
        print("No target found")
        exit()

    for m in memories:
        if m["object"] != target:
                new_memories.append(m)

    with open("memory.jsonl", "w", encoding="utf-8") as file:
        for m in new_memories:
            file.write(json.dumps(m) + "\n")

    print("Memory deleted!")