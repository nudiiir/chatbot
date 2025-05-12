# DoppioBot (Now Powered by Google Gemma)

<https://user-images.githubusercontent.com/34810212/233836702-c626bd91-4016-4731-89b0-a09c21e433c4.mp4>

Use. Play. Extend. A Generative AI chat experience, built right into Frappe's desk interface, now leveraging Google's Gemma models via the Gemini API.

## Announcement Blog Post

You can read more on how the original DoppioBot was built, how to use it and how to extend it for your own applications in [this](https://frappe.io/blog/engineering/introducing-doppiobot-template) blog post. (Note: This post refers to the original OpenAI integration).

## Tech Stack

- [Frappe Framework](https://frappeframework.com)
  - Python & JavaScript
  - MariaDB
  - Redis
- [LangChain](https://python.langchain.com/en/latest/)
- [Google Generative AI API (for Gemma models)](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api)
- [ReactJS](https://reactjs.org)
- [ChakraUI](https://chakra-ui.com)

## Installation & Usage

Just like any other Frappe app, if you have bench installed, you can execute the following commands to install the **DoppioBot** app on your Frappe site:

```bash
bench get-app soportechappsa/CubeBot # Or your forked repository
bench --site <your-site> install-app doppio_bot
```

Then add your Google API key to the `site_config.json` (of the site you have installed the app on):

```json
"google_api_key": "YOUR_GOOGLE_API_KEY"
```

Navigate to DoppioBot Settings in your Frappe desk to select the desired Google Gemma model (e.g., "models/gemma-3-27b-it").

Then navigate to your site, use the awesome bar for **Ask DoppioBot**, and enjoy!

### Chat Interface

![doppio_bot_cover_image](https://user-images.githubusercontent.com/34810212/233837411-68359b1d-8a5a-4f7e-bf13-45f534cb6d64.png)

The Chat page is built using Frappe's Custom Pages feature, React, and ChakraUI.

## Features

![DoppioBot Feature Sneak](https://user-images.githubusercontent.com/34810212/233836622-eac2011c-f84d-476d-926f-2e08da2b396d.png)

- Session Chat history management with Redis
- Formatting of markdown responses including tables and lists
- Code block responses are syntax-highlighted and have a click to copy button!
- A sleek loading skeleton is shown while the message is being fetched
- The prompt can be submitted through mouse as well as keyboard (`Cmd + Enter`)


### API

![bot_fun_chat](https://user-images.githubusercontent.com/34810212/233836619-7d8eca87-a177-4659-bef1-7dbbf699cca7.png)

The API that powers the chat page is built using the LangChain Python package with Google's Generative AI models.

## Advanced Example: Agent with Custom Tool

Here is an example of a conversational agent that uses a custom tool that creates a ToDo document in the Frappe backend:

```python
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import tool
from langchain.agents import AgentType
from langchain.memory import ConversationBufferMemory
from langchain.agents import initialize_agent

# Ensure your GOOGLE_API_KEY is set in the environment or passed to the constructor
llm = ChatGoogleGenerativeAI(model="models/gemma-3-27b-it", temperature=0)
memory = ConversationBufferMemory(memory_key="chat_history")
tools = [create_todo]

agent_chain = initialize_agent(
 tools,
 llm,
 agent=AgentType.CONVERSATIONAL_REACT_DESCRIPTION,
 verbose=True,
 memory=memory,
)

# Will call the tool with proper JSON and voila, magic!
agent_chain.run("I have to create a college report before May 17, 2023, can you set a task for me?")
```

The tool that creates new `ToDo` documents in Frappe:

```python
import frappe # Assuming this runs within a Frappe environment

@tool
def create_todo(todo: str) -> str:
 """
 Create a new ToDo document, can be used when you need to store a note or todo or task for the user.
 It takes a json string as input and requires at least the `description`. Returns "done" if the
 todo was created and "failed" if the creation failed. Optionally it could contain a `date`
 field (in the JSON) which is the due date or reminder date for the task or todo. The `date` must follow
 the "YYYY-MM-DD" format. You don\'t need to add timezone to the date.
 """
 try:
  data = frappe.parse_json(todo)
  todo_doc = frappe.new_doc("ToDo") # Renamed variable to avoid conflict
  todo_doc.update(data)
  todo_doc.save()
  return "done"
 except Exception:
  return "failed"
```

Learn more about creating custom tools [here](https://python.langchain.com/en/latest/modules/agents/tools/custom_tools.html).

#### License

MIT

# chatbot
