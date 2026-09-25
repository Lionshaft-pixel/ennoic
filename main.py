import json

time = ""
person = ""
object = ""
mode = input("Would you like to:" + "\n" "1: Remember Something" + "\n" + "2: Ask Something" + "\n" "You: ")
if mode.lower() == "remember something":
        message = input("You: ")
        print("Got it!")

        if "tomorrow" in message or "next week" in message or "next month" in message:
            print("This might be important")
        else:       
            print("Not important")

        if "tomorrow" in message:
            time = "tomorrow"
        elif "next week" in message:
            time = "next week"
        elif "next month" in message:
            time = "next month"
        else:
            print("No time detected")

        if "Alex" in message or "alex" in message:
            person = "Alex"
        elif "Jaydon" in message or "jaydon" in message:
            person = "Jaydon"
        elif "Steve" in message or "steve" in message:
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
            
        with open("memory.jsonl", "a", encoding="utf-8") as file:
                file.write(json.dumps(memory) + "\n")

elif mode.lower() == "ask something":
    memories = []
    with open("memory.jsonl", "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                memories.append(json.loads(line))

    print("Sure! Ask whatever you want to!")

    question = input("You: ")
    if "who" in question:
        intent = "who"
    elif "when" in question:
        intent = "when"

    if "key" in question:
        target = "keys"
    elif "car" in question:
        target = "car"
    elif "ticket" in question:
        target = "ticket"

    for m in memories:
        if m["object"] == target:
            if intent == "who":
                print(m["person"])
            elif intent == "when":
                print(m["time"])

else:
    print("Please choose between two options!")