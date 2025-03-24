import os
import json
from openai import OpenAI

# Get the API key from the environment
api_key = os.environ.get("OPENAI_API_KEY")

if not api_key:
    print(json.dumps({"status": "error", "message": "OpenAI API key is missing"}))
    exit(1)

try:
    # Create a client
    client = OpenAI(api_key=api_key)
    
    # Try a simple completion to test the API key
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=10
    )
    
    content = response.choices[0].message.content
    
    # Print the result as JSON
    print(json.dumps({
        "status": "success",
        "message": "OpenAI API key is valid and working properly",
        "response": content
    }))
except Exception as e:
    print(json.dumps({
        "status": "error",
        "message": f"Error testing OpenAI API: {str(e)}"
    }))
    exit(1)
