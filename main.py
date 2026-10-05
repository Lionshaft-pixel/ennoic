import json
import requests

message = input("You: ")
print("Thinking...")
response = requests.post(
    "http://localhost:11434/api/generate",
    json={
        "model": "qwen2.5:1.5b",
        "prompt": f"""
    Return exactly one JSON object.
    The JSON object must contain exactly these five fields:
    "action"
    "intent"
    "person"
    "object"
    "time"

    Never omit any field.
    Never add any other field.
    If a field has no value, use an empty string "".

    Allowed values:

    "action":
    * "remember" = the user gives Ennoic new information to store.
    * "ask" = the user asks Ennoic to retrieve information from memory.
    * "forget" = the user wants Ennoic to remove information from memory.
    * "update" = the user wants Ennoic to change information already in memory.

    "intent":
    * "who" = the user wants to know a person.
    * "when" = the user wants to know a time.
    * "" = no retrieval intent.

    "person":
    * The person's name mentioned in the message.
    * Use "" if no person is mentioned.

    "object":
    * The thing, subject, or item the message is about.
    * Use "" if no object can be identified.

    "time":
    * The time mentioned in the message.
    * Use "" if no time is mentioned.

    Rules:
    1. Understand the meaning of the entire user message.
    2. Do not guess or invent information.
    3. A question about something Ennoic may already remember is "ask".
    4. New information that should be stored is "remember".
    5. A request to remove remembered information is "forget".
    6. A request to change remembered information is "update".
    7. For "ask", do not invent a time.
    8. "When" questions have intent "when".
    9. "Who" questions have intent "who".
    10. For "remember", intent should be "" unless the user is explicitly asking a question.
    11. The value of "time" must be "" when the user does not provide a time.
    12. The value of "person" must be "" when the user does not provide a person.
    13. The value of "object" must be "" when no object can be identified.
    14. Output valid JSON only.
    15. Do not use Markdown.
    16. Do not use code fences.
    17. Do not explain your answer.
    18. Start the response with {{ and end the response with }}.

    Examples:
    User: Alex is sending me the keys tomorrow.
    Output:
    {{"action":"remember","intent":"","person":"Alex","object":"keys","time":"tomorrow"}}

    User: When is Alex sending the keys?
    Output:
    {{"action":"ask","intent":"when","person":"Alex","object":"keys","time":""}}

    User: Who has the keys?
    Output:
    {{"action":"ask","intent":"who","person":"","object":"keys","time":""}}

    User: Steve is bringing the car next week.
    Output:
    {{"action":"remember","intent":"","person":"Steve","object":"car","time":"next week"}}

    User: Forget the keys.
    Output:
    {{"action":"forget","intent":"","person":"","object":"keys","time":""}}

    User: Alex is sending the keys next Friday instead.
    Output:
    {{"action":"update","intent":"","person":"Alex","object":"keys","time":"next Friday"}}

    Now process this user message: {message}""",
    "stream": False,
    "options": {
        "temperature": 0
    }
    }
)

ai_response = response.json()["response"]
ai_response = ai_response.strip().removeprefix("```json").removesuffix("```").strip()
print(repr(ai_response))
ai_data = json.loads(ai_response)

mode = ai_data["action"]
memories = []

try:
    with open("memory.jsonl", "r") as file:
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

if mode == "remember":
    memory = {
        "person": person,
        "object": object,
        "time": time
        }

    match = False

    for m in memories:
        if m["person"] == person and m["object"] == object:
            m["time"] = time
            match = True

    if match:
        with open("memory.jsonl", "w", encoding="utf-8") as file:
            file.write(json.dumps(m) + "\n")

    else:
        with open("memory.jsonl", "a", encoding="utf-8") as file:
            file.write(json.dumps(memory) + "\n")

    print("Stored new info")

elif mode == "ask":
    intent = ai_data["intent"]
    target = ai_data["object"]
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
        print("Memory does not exist!")
        
elif mode == "forget": # It is only for forgetting an object.
    new_memories = []
    target = ai_data["object"]

    for m in memories:
        if m["object"] != target:
                new_memories.append(m)

    with open("memory.jsonl", "w", encoding="utf-8") as file:
        for m in new_memories:
            file.write(json.dumps(m) + "\n")

    print("Memory deleted!")

else:
    print("Invalid mode!")