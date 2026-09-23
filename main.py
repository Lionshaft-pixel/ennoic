message = input("You: ")
print("Got it!")
time = ""
person = ""
object = ""

if "tomorrow" in message or "next week" in message or "next month" in message:
    print("This might be important")
else:
    print("Not important")

question = input("You: ")

if "tomorrow" in message:
    time = "tomorrow"
elif "next week" in message:
    time = "next week"
elif "next month" in message:
    time = "next month"
else:
    print("No time detected")

if "Alex" in message:
    person = "Alex"
elif "Jaydon" in message:
    person = "Jaydon"
elif "Steve" in message:
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

with open("memory.txt", "w", encoding="utf-8") as file:
    file.write(f"{time}\n{person}\n{object}")

if "What is Alex sending?" in question:
    with open("memory.txt", "r") as file:
        lines = file.readlines()
    time = lines[0].strip()
    person = lines[1].strip()
    object = lines[2].strip()

    print(f"{person} is sending {object} to you {time}.")
else:
    print("I'm sorry, I don't know.")