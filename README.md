# Ennoic

AI powered memory tool that lets you store stuff, retrieve it, and forget information using natural language.

Originally, the idea was to build this for mobile devices since, you know, billions of people use them. So I started with Python because that's what I know.

---

## What It Does

Ennoic is a memory assistant (for now). You tell it stuff, it remembers. You ask it stuff, it answers. You tell it to forget stuff, it removes it from memory. Simple.

It uses an LLM to interpret whatever you type and convert that into structured data (action, intent, person, object, and time) that gets stored in a local file. When you ask a question later, it searches that file and gives you an answer.

It can run on a local model (via Ollama) or a cloud model. Me, personally, I use Groq's API, but you can change the model if you want. You'll notice the code is set to cloud mode by default.

The LLM can misunderstand whatever you type or extract the wrong information. So, even if the memory system works correctly, it MIGHT store something that wasn't what you meant.

---

## The Flow

1. If you type something like, *"Remember that Alex has the keys tomorrow."*

2. The AI parses it into structured JSON data:

   ```json
   {"action": "remember", "intent": "...", "person": "Alex", "object": "keys", "time": "tomorrow"}
   ```


3. And that gets saved to `memory.jsonl`, which is, of course, one JSON object per line or call it JSONL.

4. Next time you ask, *"Who has the keys?"*, it reads the file, finds the match, and answers something like, *"Alex has the keys."*

---

## Actions

- **remember**: store new info or update existing info
- **ask**: retrieve info (like who has something or when something happens)
- **forget**: remove matching information from stored memory
- **unknown**: inputs that don't fit the memory system, like gibberish, greetings, or unrelated questions

`unknown` doesn't mean Ennoic can answer random questions like a general purpose chatbot. It's only there for handling when none of these four action fields matches with input.

---

## Storage

All persistent memory is stored in `memory.jsonl`. Each line contains a JSON object representing a memory entry. A simplified example looks like this:

```json
{"person": "Alex", "object": "keys", "time": "tomorrow"}
```

The actual structure can also include fields such as `action` and `intent`, depending on how the information is processed.

So if you say, *"Alex has the keys next Friday"* and Alex already has the keys stored, it updates the time instead of creating a duplicate entry. If it's a new person/object combination, it appends a new line.

When you use the forget action, Ennoic removes matching entries from `memory.jsonl`. This applies to the stored memory file, not every copy of that information that might exist elsewhere.

---

## LLM Modes

**Local mode** uses Ollama at `http://localhost:11434/api/generate` with a small model like `qwen2.5:1.5b` (as configured in the code). Good for keeping model processing on your own machine, but smaller models can struggle with complicated inputs.

**Cloud mode** uses Groq's API with `openai/gpt-oss-120b`, which can handle more complicated inputs, but it requires an API key and an internet connection. Your prompts are sent to the cloud provider when you use this mode, so keep that in mind if you're storing sensitive information.

---

## Setup

You'll need:

- Python 3
- The `requests` library
- Ollama and a compatible model if you're using local mode
- A `GROQ_API_KEY` environment variable if you're using cloud mode

Cloud mode is the default in the current code, so you'll need a valid Groq API key for it to work.

Install the dependency:

```bash
pip install requests
```

### Setting up your API key

Get an API key from [Groq Console](https://console.groq.com/keys). Keep it private and **never put it directly into your source code or commit it to GitHub**.

On Windows PowerShell, you can set it for your current terminal session like this:

```powershell
$env:GROQ_API_KEY = "your_api_key_here"
```

On Linux, you can set it for your current terminal session like this:

```bash
export GROQ_API_KEY="your_api_key_here"
```

Replace `your_api_key_here` with your actual key. These commands set the variable for the current terminal session. If you want it available permanently, configure a user environment variable through your operating system's settings.

### Running Ennoic

From the project directory, run:

```bash
python main.py
```

Or on Linux:

```bash
python3 main.py
```

---

## Exceptions

Ennoic is an early prototype, so there's still plenty to improve.

- It supports a limited set of memory actions rather than general purpose conversations.
- It stores persistent memory in a local JSONL file instead of a database but I'm working on it.
- Its accuracy depends partly on how well the selected LLM interprets your input.
- Incorrect extraction can lead to incorrect memories or answers.

The goal is to build toward a more capable assistant. For now, this is the memory engine I'm starting with.
