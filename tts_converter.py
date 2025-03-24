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
        text = conversion.text
        chunks = []
        
        # Try to split at sentence boundaries
        sentences = text.replace('\n', ' ').split('. ')
        current_chunk = ""
        
        for sentence in sentences:
            # Add period back except for the last sentence if it doesn't end with period
            if sentence != sentences[-1] or text.endswith('.'):
                sentence += '.'
            
            # If adding this sentence would exceed chunk size, store current chunk and start a new one
            if len(current_chunk) + len(sentence) > 4000:
                chunks.append(current_chunk.strip())
                current_chunk = sentence + " "
            else:
                current_chunk += sentence + " "
        
        # Add the last chunk if not empty
        if current_chunk.strip():
            chunks.append(current_chunk.strip())
        
        chunking_end = time.time()
        metrics.chunking_time = chunking_end - chunking_start
        metrics.chunk_count = len(chunks)
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
        
        # Set up OpenAI client
        client = AsyncOpenAI(api_key=app.config["OPENAI_API_KEY"])
        
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
            # Call the OpenAI API to generate speech
            try:
                response = await client.audio.speech.create(
                    model="tts-1",
                    voice="alloy",  # You can change this to other available voices
                    input=text
                )
                logger.info(f"OpenAI API call successful for chunk {chunk_index}, conversion_id: {conversion_id}")
            except Exception as e:
                logger.error(f"OpenAI API call failed: {str(e)}")
                logger.error(f"Traceback: {traceback.format_exc()}")
                raise
            
            # Check if the conversion was cancelled during the API call
            if should_cancel(conversion_id):
                logger.info(f"Conversion cancelled during API call for chunk {chunk_index}")
                return
            
            # Save the audio data to a temporary file
            temp_file_path = os.path.join(audio_dir, f"{chunk_index}_chunk.mp3")
            try:
                with open(temp_file_path, 'wb') as f:
                    # The response is a bytes object, so we can write it directly to a file
                    logger.info(f"Reading response data for chunk {chunk_index}")
                    audio_data = await response.read()
                    logger.info(f"Writing {len(audio_data)} bytes to file for chunk {chunk_index}")
                    f.write(audio_data)
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
