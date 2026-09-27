import json

time = ""
person = ""
object = ""
mode = input("Would you like to:" + "\n" "1: Remember Something" + "\n" + "2: Ask Something" + "\n" "You: ")
if mode.lower() == "remember something":
        message = input("You: ")
        message = message.lower()
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
        
        with open("memory.jsonl", "a", encoding="utf-8") as file:
                file.write(json.dumps(memory) + "\n")

elif mode.lower() == "ask something":
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

    print("Sure! Ask whatever you want to!")

    question = input("You: ")
    question = question.lower()

    if "who" in question:
        intent = "who"
    elif "when" in question:
        intent = "when"
    else:
        print("No intent found")
        exit()

    if "keys" in question:
        target = "keys"
    elif "car" in question:
        target = "car"
    elif "ticket" in question:
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

    if not found:
        print("Memory not found!")
else:
    print("Please choose between two options!")