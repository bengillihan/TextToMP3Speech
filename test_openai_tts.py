import os
import json
from openai import OpenAI
import tempfile

# Get the API key from the environment
api_key = os.environ.get("OPENAI_API_KEY")

if not api_key:
    print(json.dumps({"status": "error", "message": "OpenAI API key is missing"}))
    exit(1)

try:
    # Create a client
    client = OpenAI(api_key=api_key)
    
    # Create a temporary file to store the audio
    temp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    temp_file.close()
    
    # Try a simple TTS API call
    response = client.audio.speech.create(
        model="tts-1",
        voice="alloy",
        input="Hello, this is a test of the OpenAI TTS API."
    )
    
    # Save the audio to the temporary file
    response.stream_to_file(temp_file.name)
    
    # Check if the file exists and has content
    file_size = os.path.getsize(temp_file.name)
    
    # Print the result as JSON
    print(json.dumps({
        "status": "success",
        "message": "OpenAI TTS API is valid and working properly",
        "file_path": temp_file.name,
        "file_size": file_size
    }))
except Exception as e:
    print(json.dumps({
        "status": "error",
        "message": f"Error testing OpenAI TTS API: {str(e)}"
    }))
    exit(1)
