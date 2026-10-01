import json

time = ""
person = ""
object = ""
message = input("You: ")
message = message.lower()
memories = []

if "?" in message or "who" in message or "when" in message:
    mode = "ask"
elif "forget" in message:
    mode = "forget"
else:
    mode = "remember"

try:
    with open("memory.jsonl", "r", encoding="utf-8") as file: # This is unneccessary for remember branch
        for line in file:
            line = line.strip()
            if line:
                memories.append(json.loads(line))
except FileNotFoundError:
        print("Memory file does not exist!")
        exit()
    

if mode == "remember":
    if "tomorrow" in message:
        time = "tomorrow"
    elif "next week" in message:
        time = "next week"
    elif "next month" in message:
        time = "next month"
    else:
        print("No time detected")

    if "alex" in message:
        person = "Alex"
    elif "jaydon" in message:
        person = "Jaydon"
    elif "steve" in message:
        person = "Steve"
    else:
        print("No name detected")

    if "ticket" in message:
        object = "ticket"
    elif "car" in message:
        object = "car"
    elif "keys" in message or "key" in message:
        object = "keys"
    else:
        print("No object detected")

    memory = {
            "time": time,
            "person": person,
            "object": object
        }
    
    for m in memories:
        if m["person"] == person and m["object"] == object:
            m["time"] = time
    print(memories)

    with open("memory.jsonl", "w", encoding="utf-8") as file:
        for m in memories:
            file.write(json.dumps(m) + "\n")

    print("Stored new info")

elif mode == "ask":
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