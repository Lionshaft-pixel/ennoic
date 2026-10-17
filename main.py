import json
from models import ask_model, ask_cloud_model

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

model_mode = "cloud"

prompt = ""
try:
    with open("prompt.txt", "r", encoding="utf-8") as file:
        prompt = file.read()
except FileNotFoundError:
    print("Prompt file not Found!")
    raise SystemExit

prompt = prompt.replace("{{MEMORIES}}", str(memories))
prompt = prompt.replace("{{MESSAGE}}", message)

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