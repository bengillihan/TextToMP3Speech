import os
import json
import asyncio
from openai import AsyncOpenAI
import tempfile

# Test async OpenAI client with TTS API
async def test_async_tts():
    # Get the API key from the environment
    api_key = os.environ.get("OPENAI_API_KEY")
    
    if not api_key:
        print(json.dumps({"status": "error", "message": "OpenAI API key is missing"}))
        return 1
    
    try:
        # Create an async client with timeout
        client = AsyncOpenAI(
            api_key=api_key,
            timeout=60.0  # 60 second timeout
        )
        
        # Create a temporary file to store the audio
        temp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        temp_file.close()
        
        # Try a simple TTS API call
        print("Making async API call...")
        response = await client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input="Hello, this is a test of the OpenAI TTS API using AsyncOpenAI client."
        )
        
        # Investigate the response
        print(f"Response type: {type(response)}")
        print(f"Response attributes: {dir(response)}")
        
        # Save the audio data to a file
        print("Reading content...")
        # Use the read() method but do not await it
        content = response.read()
        print(f"Content type: {type(content)}")
        print(f"Content length: {len(content) if content else 'None'}")
        
        print("Writing response to file...")
        with open(temp_file.name, 'wb') as f:
            f.write(content)
        
        # Check if the file exists and has content
        file_size = os.path.getsize(temp_file.name)
        
        # Print the result as JSON
        print(json.dumps({
            "status": "success",
            "message": "AsyncOpenAI TTS API is valid and working properly",
            "file_path": temp_file.name,
            "file_size": file_size
        }))
        return 0
    except Exception as e:
        import traceback
        print(f"Error: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        print(json.dumps({
            "status": "error",
            "message": f"Error testing AsyncOpenAI TTS API: {str(e)}"
        }))
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(test_async_tts())
    exit(exit_code)
