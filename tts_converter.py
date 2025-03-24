import os
import asyncio
import aiohttp
import time
import logging
import uuid
import traceback
from datetime import datetime
from pydub import AudioSegment
from threading import Thread

# Import the OpenAI client - using the latest SDK
from openai import AsyncOpenAI, OpenAI
from app import app, db
from models import Conversion, ConversionMetrics, APILog

logger = logging.getLogger(__name__)

# Global dictionary to keep track of cancellation requests
cancellation_requests = {}

def cancel_conversion(conversion_id):
    """Mark a conversion for cancellation"""
    cancellation_requests[conversion_id] = True
    
    # Update the conversion status in the database
    with app.app_context():
        conversion = Conversion.query.get(conversion_id)
        if conversion and conversion.status in ['pending', 'processing']:
            conversion.status = 'cancelled'
            conversion.updated_at = datetime.utcnow()
            db.session.commit()
            return True
    return False

def should_cancel(conversion_id):
    """Check if a conversion has been marked for cancellation"""
    return cancellation_requests.get(conversion_id, False)

def process_conversion(conversion_id):
    """Start the conversion process in a background thread"""
    thread = Thread(target=_process_conversion_thread, args=(conversion_id,))
    thread.daemon = True
    thread.start()
    return thread

def _process_conversion_thread(conversion_id):
    """Thread function to process a conversion"""
    logger.info(f"Starting conversion thread for conversion_id: {conversion_id}")
    try:
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Log that we're about to start the conversion process
        logger.info(f"About to start conversion process for conversion_id: {conversion_id}")
        
        # Run the conversion process
        loop.run_until_complete(_process_conversion(conversion_id))
        
        logger.info(f"Conversion process completed for conversion_id: {conversion_id}")
    except Exception as e:
        import traceback
        logger.error(f"Error in conversion thread: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        
        with app.app_context():
            logger.info(f"Updating database with error for conversion_id: {conversion_id}")
            conversion = Conversion.query.get(conversion_id)
            if conversion:
                conversion.status = 'failed'
                db.session.add(APILog(
                    conversion_id=conversion_id,
                    type='error',
                    message=f"Thread error: {str(e)}"
                ))
                db.session.commit()
                logger.info(f"Database updated with error for conversion_id: {conversion_id}")
    finally:
        # Clean up
        if conversion_id in cancellation_requests:
            del cancellation_requests[conversion_id]
        logger.info(f"Conversion thread for conversion_id: {conversion_id} completed")

async def _process_conversion(conversion_id):
    """Process the text-to-speech conversion using OpenAI API"""
    logger.info(f"Starting _process_conversion for conversion_id: {conversion_id}")
    start_time = time.time()
    chunking_start = time.time()
    
    with app.app_context():
        logger.info(f"Entering app context for conversion_id: {conversion_id}")
        # Get the conversion from the database
        conversion = Conversion.query.get(conversion_id)
        if not conversion:
            logger.error(f"Conversion with ID {conversion_id} not found")
            return
        
        logger.info(f"Found conversion in database: {conversion.id}, title: {conversion.title}")
        
        # Initialize metrics
        metrics = ConversionMetrics(conversion_id=conversion_id)
        db.session.add(metrics)
        
        # Update conversion status
        conversion.status = 'processing'
        conversion.progress = 0.0
        conversion.updated_at = datetime.utcnow()
        db.session.commit()
        
        # Split text into chunks of 4000 characters or less
        text = conversion.text or ""  # Ensure text is not None
        logger.info(f"Starting text chunking for conversion {conversion_id}, text length: {len(text)} characters")
        
        # Validate that we have text to process
        if not text.strip():
            error_message = "No text content provided for conversion"
            logger.error(error_message)
            conversion.status = 'failed'
            db.session.add(APILog(
                conversion_id=conversion_id,
                type='error',
                message=error_message
            ))
            db.session.commit()
            return
            
        chunks = []
        max_chunk_size = 4000
        
        # More robust chunking algorithm that handles various text formats
        # First, normalize line endings and replace multiple spaces with single spaces
        logger.info("Normalizing text format")
        normalized_text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # Try to split at sentence boundaries (looking for various sentence-ending punctuation)
        logger.info("Splitting text at sentence boundaries")
        
        # Use a regex to split on common sentence boundaries
        import re
        # Match periods, question marks, exclamation points followed by space or newline
        sentence_pattern = r'(?<=[.!?])\s+'
        sentences = re.split(sentence_pattern, normalized_text)
        
        # If we didn't get any meaningful split, just split by newlines
        if len(sentences) <= 1:
            logger.info("No sentence boundaries found, splitting by paragraphs")
            sentences = normalized_text.split('\n')
            
            # If still no meaningful split, do a basic character split
            if len(sentences) <= 1:
                logger.info("No paragraph breaks found, doing basic character chunking")
                # Just divide the text into chunks of 3500 characters to be safe
                chunk_size = 3500
                sentences = [normalized_text[i:i+chunk_size] for i in range(0, len(normalized_text), chunk_size)]
        
        logger.info(f"Split text into {len(sentences)} sentence/paragraph fragments")
        
        current_chunk = ""
        
        for i, sentence in enumerate(sentences):
            # Ensure the sentence is not empty
            if not sentence.strip():
                continue
                
            # Log every 50 sentences to avoid excessive logging
            if i % 50 == 0:
                logger.debug(f"Processing sentence {i+1}/{len(sentences)}, length: {len(sentence)} characters")
            
            # Make sure we don't exceed the maximum chunk size
            if len(sentence) > max_chunk_size:
                logger.warning(f"Sentence {i+1} exceeds max chunk size ({len(sentence)} chars), splitting")
                # Split the sentence into smaller parts
                for j in range(0, len(sentence), max_chunk_size - 100):  # -100 for safety margin
                    sub_sentence = sentence[j:j+max_chunk_size-100]
                    if sub_sentence.strip():
                        chunks.append(sub_sentence.strip())
                        logger.debug(f"Added long sentence chunk with {len(sub_sentence.strip())} characters")
            else:
                # Normal case: add sentence to current chunk if it fits, otherwise start a new chunk
                if len(current_chunk) + len(sentence) > max_chunk_size:
                    if current_chunk.strip():
                        chunks.append(current_chunk.strip())
                        logger.debug(f"Added chunk {len(chunks)} with {len(current_chunk.strip())} characters")
                    current_chunk = sentence + " "
                else:
                    current_chunk += sentence + " "
        
        # Add the last chunk if not empty
        if current_chunk.strip():
            chunks.append(current_chunk.strip())
            logger.debug(f"Added final chunk {len(chunks)} with {len(current_chunk.strip())} characters")
        
        # Last sanity check - if we still have no chunks but have text, create at least one chunk
        if not chunks and text.strip():
            logger.warning("Chunking algorithms produced no chunks, creating a single fallback chunk")
            # Take the first 4000 chars at most to ensure we have something to process
            chunks.append(text.strip()[:max_chunk_size])
        
        chunking_end = time.time()
        metrics.chunking_time = chunking_end - chunking_start
        metrics.chunk_count = len(chunks)
        
        # Log chunking details
        logger.info(f"Text chunking complete. Created {len(chunks)} chunks")
        if chunks:
            logger.info(f"Chunks range in size from {min(len(chunk) for chunk in chunks)} " +
                      f"to {max(len(chunk) for chunk in chunks)} characters")
        else:
            logger.error("No chunks were created from the input text!")
            
        # Count tokens (words) for metrics
        word_count = sum(len(chunk.split()) for chunk in chunks)
        metrics.total_tokens = word_count
        logger.info(f"Total words: {word_count}, chunking time: {metrics.chunking_time:.2f} seconds")
        
        # Verify that we have chunks to process
        if not chunks:
            error_message = "No chunks were created from the input text"
            logger.error(error_message)
            conversion.status = 'failed'
            db.session.add(APILog(
                conversion_id=conversion_id,
                type='error',
                message=error_message
            ))
            db.session.commit()
            return
            
        db.session.commit()
        
        # Check if the user has cancelled the conversion
        if should_cancel(conversion_id):
            logger.info(f"Conversion {conversion_id} was cancelled")
            return
        
        # Process each chunk in parallel
        api_start = time.time()
        
        # Create audio directory if it doesn't exist
        audio_dir = os.path.join(app.config["AUDIO_STORAGE_PATH"], str(uuid.uuid4()))
        os.makedirs(audio_dir, exist_ok=True)
        
        # Set up OpenAI client with timeout
        client = AsyncOpenAI(
            api_key=app.config["OPENAI_API_KEY"],
            timeout=60.0  # 60 second timeout for API calls
        )
        logger.info("AsyncOpenAI client created with timeout settings")
        
        # Create a list to store the paths of the temporary audio files
        temp_audio_files = []
        
        # Process chunks in parallel
        try:
            tasks = []
            for i, chunk in enumerate(chunks):
                tasks.append(process_chunk(client, conversion_id, i, chunk, audio_dir, temp_audio_files))
            
            # Wait for all tasks to complete
            await asyncio.gather(*tasks)
            
            # Update API time in metrics
            api_end = time.time()
            metrics.api_time = api_end - api_start
            db.session.commit()
            
            # Check if the user has cancelled the conversion
            if should_cancel(conversion_id):
                logger.info(f"Conversion {conversion_id} was cancelled during API calls")
                # Clean up temporary files
                for file_path in temp_audio_files:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                os.rmdir(audio_dir)
                return
            
            # Combine audio chunks into a single file
            combining_start = time.time()
            
            # Sort the temporary files by their chunk index
            temp_audio_files.sort(key=lambda x: int(os.path.basename(x).split('_')[0]))
            
            # Check if we have any audio files to combine
            if not temp_audio_files:
                conversion.status = 'failed'
                db.session.add(APILog(
                    conversion_id=conversion_id,
                    type='error',
                    message="No audio files were generated"
                ))
                db.session.commit()
                return
            
            # Combine audio files
            logger.info(f"Starting to combine {len(temp_audio_files)} audio files")
            combined = AudioSegment.empty()
            for i, file_path in enumerate(temp_audio_files):
                if os.path.exists(file_path):
                    try:
                        logger.info(f"Loading audio file {i+1}/{len(temp_audio_files)}: {file_path}")
                        audio_chunk = AudioSegment.from_file(file_path, format="mp3")
                        logger.info(f"Audio file {i+1} loaded, duration: {len(audio_chunk)/1000:.2f} seconds")
                        combined += audio_chunk
                    except Exception as e:
                        logger.error(f"Error loading audio file {file_path}: {str(e)}")
                        logger.error(f"Traceback: {traceback.format_exc()}")
                        # Continue with other files
                else:
                    logger.warning(f"Audio file {file_path} does not exist")
            
            # Generate the output file path
            output_filename = f"{conversion.uuid}.mp3"
            output_path = os.path.join(app.config["AUDIO_STORAGE_PATH"], output_filename)
            logger.info(f"Generated output path: {output_path}")
            
            # Export the combined audio file
            logger.info(f"Exporting combined audio file, total duration: {len(combined)/1000:.2f} seconds")
            combined.export(output_path, format="mp3")
            logger.info(f"Combined audio file exported successfully to {output_path}")
            
            # Clean up temporary files
            for file_path in temp_audio_files:
                if os.path.exists(file_path):
                    os.remove(file_path)
            os.rmdir(audio_dir)
            
            combining_end = time.time()
            metrics.combining_time = combining_end - combining_start
            
            # Update conversion status and file path
            conversion.status = 'completed'
            conversion.progress = 100.0
            conversion.file_path = output_path
            conversion.updated_at = datetime.utcnow()
            
            # Update total time in metrics
            metrics.total_time = time.time() - start_time
            
            db.session.commit()
            logger.info(f"Conversion {conversion_id} completed successfully")
            
        except Exception as e:
            logger.error(f"Error during conversion: {str(e)}")
            conversion.status = 'failed'
            db.session.add(APILog(
                conversion_id=conversion_id,
                type='error',
                message=f"Error during conversion: {str(e)}"
            ))
            db.session.commit()
            
            # Clean up temporary files
            for file_path in temp_audio_files:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
            try:
                os.rmdir(audio_dir)
            except Exception:
                pass

async def process_chunk(client, conversion_id, chunk_index, text, audio_dir, temp_audio_files):
    """Process a single text chunk with the OpenAI TTS API"""
    logger.info(f"Processing chunk {chunk_index} for conversion_id: {conversion_id}")
    try:
        with app.app_context():
            logger.info(f"Entering app context for chunk {chunk_index}, conversion_id: {conversion_id}")
            conversion = Conversion.query.get(conversion_id)
            if should_cancel(conversion_id):
                logger.info(f"Chunk {chunk_index} cancelled for conversion_id: {conversion_id}")
                return
            
            # Log OpenAI API key status (without revealing the key)
            logger.info(f"Checking OpenAI API key for chunk {chunk_index}")
            api_key = app.config.get("OPENAI_API_KEY")
            if not api_key:
                error_msg = "OpenAI API key is missing"
                logger.error(error_msg)
                raise ValueError(error_msg)
            logger.info("OpenAI API key is available")
            
            logger.info(f"Calling OpenAI API for chunk {chunk_index}, conversion_id: {conversion_id}")
            # Call the OpenAI API to generate speech with retries and exponential backoff
            max_retries = 3
            retry_delay = 1.0  # initial delay in seconds
            
            for retry in range(max_retries + 1):  # +1 for the initial attempt
                try:
                    if retry > 0:
                        logger.warning(f"Retry {retry}/{max_retries} for chunk {chunk_index} after {retry_delay:.1f}s delay")
                        # Wait with exponential backoff
                        await asyncio.sleep(retry_delay)
                        # Double the delay for the next retry (exponential backoff)
                        retry_delay *= 2
                    
                    # Make the API call
                    response = await client.audio.speech.create(
                        model="tts-1",
                        voice="alloy",  # You can change this to other available voices
                        input=text
                    )
                    logger.info(f"OpenAI API call successful for chunk {chunk_index}, conversion_id: {conversion_id}")
                    
                    # If we get here, the call was successful, so break out of the retry loop
                    break
                    
                except Exception as e:
                    logger.error(f"OpenAI API call failed: {str(e)}")
                    
                    # Log to the database
                    with app.app_context():
                        db.session.add(APILog(
                            conversion_id=conversion_id,
                            type='warning',
                            message=f"API call failed for chunk {chunk_index}: {str(e)}",
                            chunk_index=chunk_index,
                            status=500
                        ))
                        db.session.commit()
                    
                    # If this was our last retry attempt, log and raise
                    if retry == max_retries:
                        logger.error(f"All retries failed for chunk {chunk_index}. Giving up.")
                        logger.error(f"Traceback: {traceback.format_exc()}")
                        raise
            
            # Check if the conversion was cancelled during the API call
            if should_cancel(conversion_id):
                logger.info(f"Conversion cancelled during API call for chunk {chunk_index}")
                return
            
            # Save the audio data to a temporary file
            temp_file_path = os.path.join(audio_dir, f"{chunk_index}_chunk.mp3")
            try:
                logger.info(f"Processing response for chunk {chunk_index}")
                
                # Identify the response type
                response_type = type(response).__name__
                logger.info(f"Response type: {response_type}")
                
                # Handle HttpxBinaryResponseContent from AsyncOpenAI
                if response_type == 'HttpxBinaryResponseContent':
                    logger.info("Handling HttpxBinaryResponseContent")
                    # Use the read() method (not awaited) to get the bytes content
                    audio_data = response.read()
                    logger.info(f"Read {len(audio_data)} bytes from HttpxBinaryResponseContent")
                    
                    with open(temp_file_path, 'wb') as f:
                        f.write(audio_data)
                
                # Handle bytes directly
                elif isinstance(response, bytes):
                    logger.info(f"Response is bytes, writing {len(response)} bytes to file")
                    with open(temp_file_path, 'wb') as f:
                        f.write(response)
                
                # Handle any other response type
                else:
                    logger.warning(f"Unknown response type: {response_type}, attempting multiple methods")
                    # Try multiple methods to extract data
                    if hasattr(response, 'read'):
                        logger.info("Using read() method")
                        with open(temp_file_path, 'wb') as f:
                            f.write(response.read())
                    elif hasattr(response, 'content'):
                        logger.info("Using content attribute")
                        with open(temp_file_path, 'wb') as f:
                            f.write(response.content)
                    else:
                        logger.warning("No standard methods available, trying direct write")
                        with open(temp_file_path, 'wb') as f:
                            f.write(response)
                
                logger.info(f"Audio file saved successfully for chunk {chunk_index}")
            except Exception as e:
                logger.error(f"Error saving audio file: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                raise
            
            # Add the file path to the list
            temp_audio_files.append(temp_file_path)
            logger.info(f"Added chunk {chunk_index} to temp_audio_files, current count: {len(temp_audio_files)}")
            
            # Update progress
            total_chunks = conversion.metrics.chunk_count
            conversion.progress = min(95.0, (chunk_index + 1) / total_chunks * 95.0)  # Keep some room for combining
            conversion.updated_at = datetime.utcnow()
            
            # Log success
            db.session.add(APILog(
                conversion_id=conversion_id,
                type='info',
                message=f"Successfully processed chunk {chunk_index + 1}/{total_chunks}",
                chunk_index=chunk_index,
                status=200
            ))
            
            db.session.commit()
            
    except Exception as e:
        with app.app_context():
            logger.error(f"Error processing chunk {chunk_index}: {str(e)}")
            db.session.add(APILog(
                conversion_id=conversion_id,
                type='error',
                message=f"Error processing chunk {chunk_index}: {str(e)}",
                chunk_index=chunk_index
            ))
            db.session.commit()
            raise
