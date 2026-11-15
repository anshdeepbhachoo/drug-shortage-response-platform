# test_tool.py
import json
from services.agents.llm import call

tools = [{
    "type": "function",
    "name": "search_drug_catalogue",
    "description": "Find drugs in the catalogue by name.",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}]

def search_drug_catalogue(query):
    return [{"din": "00000001", "name": "Cyclophosphamide 500 mg vial"}]

items = [{
    "role": "user",
    "content": "Supplier says the 500 mg cyclophosphamide vial is delayed 3 weeks. Which drug is it?",
}]

for step in range(5):                                   # step limit
    r = call("reasoning", "Use tools to look up facts. Be brief.", items, tools)
    print(step, r.text, r.tool_calls)
    if not r.tool_calls:                                # model gave a final answer
        break
    items += r.output_items                             # send the model's turn back
    for c in r.tool_calls:
        out = search_drug_catalogue(**c.arguments)      # your code runs the tool
        items.append({
            "type": "function_call_output",
            "call_id": c.call_id,
            "output": json.dumps(out),
        })