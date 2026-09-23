message = input("You: ")
time = ""
person = ""
object = ""

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
print(f"Time detected: {time}")

if "Alex" in message:
    person = "Alex"
elif "Jaydon" in message:
    person = "Jaydon"
elif "Steve" in message:
    person = "Steve"
else:
    print("No name detected")
print(f"Person: {person}")

if "ticket" in message:
    object = "ticket"
elif "car" in message:
    object = "car"
elif "keys" in message: # idk why it doesn't print if I add "or keys" on this line
    object = "keys"
else:
    print("No object detected")
print(f"Object: {object}")