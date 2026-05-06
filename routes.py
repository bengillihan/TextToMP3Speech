import os
import re
import time
import click
import logging
import traceback
from functools import wraps
from datetime import datetime
from flask import render_template, flash, redirect, url_for, request, jsonify, send_file, session
from flask_login import login_user, logout_user, current_user, login_required
from urllib.parse import urlparse

from app import app, db
from forms import LoginForm, RegistrationForm, ConversionForm
from models import User, Conversion, ConversionMetrics, APILog, normalize_tts_model
from tts_converter import process_conversion, cancel_conversion
from utils import (
    claim_stale_pending_conversion_for_restart,
    cleanup_expired_conversions,
    cleanup_old_files,
    ensure_database_schema,
    ensure_database_indexes,
    requeue_stale_processing_conversions,
)
from timezone_utils import format_seattle_time

logger = logging.getLogger(__name__)


def diagnostics_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not app.config.get("DIAGNOSTICS_ENABLED"):
            return jsonify({"error": "Diagnostics are disabled"}), 404

        if not current_user.is_authenticated:
            return jsonify({"error": "Authentication required"}), 401

        admin_emails = app.config.get("DIAGNOSTIC_ADMIN_EMAILS", set())
        current_email = (getattr(current_user, "email", "") or "").lower()
        if not admin_emails or current_email not in admin_emails:
            return jsonify({"error": "Admin access required"}), 403

        return view(*args, **kwargs)

    return wrapped_view


@app.cli.command("cleanup-conversions")
@click.option("--retention-days", type=int, default=None, help="Days of conversion history to keep.")
def cleanup_conversions_command(retention_days):
    """Delete expired conversions without running work during web requests."""
    retention_days = retention_days or app.config.get("CONVERSION_RETENTION_DAYS", 90)
    result = cleanup_expired_conversions(retention_days=retention_days)
    click.echo(
        "Deleted {conversions} conversions and {files} files older than {days} days.".format(
            conversions=result["conversions"],
            files=result["files"],
            days=retention_days,
        )
    )


@app.cli.command("ensure-db-indexes")
def ensure_db_indexes_command():
    """Create database indexes used by history, status, log, and cleanup queries."""
    ensured_indexes = ensure_database_indexes()
    click.echo("Ensured database indexes: {indexes}".format(
        indexes=", ".join(ensured_indexes)
    ))


@app.cli.command("ensure-db-schema")
def ensure_db_schema_command():
    """Create lightweight schema additions missing from existing deployments."""
    result = ensure_database_schema()
    changes = result["changes"] or ["no changes needed"]
    click.echo("Ensured database schema: {changes}".format(
        changes=", ".join(changes)
    ))


@app.route('/')
def index():
    # Check and log the OpenAI API key status on app startup for debugging
    api_key = app.config.get("OPENAI_API_KEY")
    logger.debug(f"OpenAI API key status: {'Available and valid format' if api_key and api_key.startswith('sk-') else 'Missing or invalid format'}")
    
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html', title='Text to Speech Converter')


@app.route('/login')
def login():
    """Render the login page"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    return render_template('login.html', title='Login')


@app.route('/register')
def register():
    """Redirect to Google login for registration"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    # Check if this request is coming from the production domain
    referer = request.headers.get('Referer', '')
    request_domain = request.host
    request_url = request.url
    oauth_domain = app.config.get("OAUTH_REDIRECT_DOMAIN", "")
    
    # Log information for debugging
    logger.debug(f"Register Referer: {referer}")
    logger.debug(f"Register Request host domain: {request_domain}")
    logger.debug(f"Register Request URL: {request_url}")
    
    # CRITICAL: If we're on the production domain, special handling needed
    if oauth_domain and oauth_domain in request_domain:
        # We are directly on the production domain
        logger.info("Configured OAuth domain detected for registration")
        # Set a marker in the session that we're on production
        session['is_production'] = True
        session['oauth_domain'] = oauth_domain
        return redirect(url_for('google_auth.login'))
    elif oauth_domain and oauth_domain in referer:
        # We got here from the production domain
        logger.info("Configured OAuth domain detected in referer for registration")
        # Set a marker in the session that we're on production
        session['is_production'] = True
        session['oauth_domain'] = oauth_domain
        return redirect(url_for('google_auth.login'))
    
    # For development environment, proceed normally
    return redirect(url_for('google_auth.login'))


@app.route('/logout')
def logout():
    """Redirect to Google logout"""
    return redirect(url_for('google_auth.logout'))


@app.route('/dashboard')
@login_required
def dashboard():
    recent_conversions = Conversion.query.filter_by(user_id=current_user.id).order_by(Conversion.created_at.desc()).limit(5).all()
    return render_template('dashboard.html', 
                          title='Dashboard', 
                          conversions=recent_conversions,
                          format_seattle_time=format_seattle_time)


@app.route('/convert', methods=['GET', 'POST'])
@login_required
def convert():
    form = ConversionForm()
    if form.validate_on_submit():
        # Use the first line of text as the title if no title was provided
        title = form.title.data
        if not title.strip():
            # Extract the first line, limited to 256 characters
            first_line = form.text.data.split('\n')[0].strip()
            if first_line:
                title = first_line[:256]
            else:
                title = "Untitled Conversion"
        
        try:
            logger.info(f"Creating new conversion record for user: {current_user.id}, title: {title}")
            # Create new conversion record
            conversion = Conversion(
                user_id=current_user.id,
                title=title,
                text=form.text.data,
                voice=form.voice.data,  # Add the selected voice
                tts_model=normalize_tts_model(form.tts_model.data),
                status='pending',
                progress=0.0
            )
            db.session.add(conversion)
            db.session.commit()
            logger.info(f"Conversion record created with ID: {conversion.id}")
            
            # Check if OpenAI API key is available
            api_key = app.config.get("OPENAI_API_KEY")
            logger.info(f"OpenAI API key status: {'Available' if api_key else 'Missing'}")
            if not api_key:
                logger.error("OpenAI API key is missing")
                flash('Conversion failed: OpenAI API key is missing. Please contact the administrator.', 'danger')
                conversion.status = 'failed'
                db.session.add(APILog(
                    conversion_id=conversion.id,
                    type='error',
                    message="OpenAI API key is missing"
                ))
                db.session.commit()
                return redirect(url_for('conversions'))
            
            # Verify key format looks valid (without revealing it)
            if not api_key.startswith('sk-'):
                logger.error("OpenAI API key format appears invalid")
                flash('Conversion failed: OpenAI API key format is invalid. Please contact the administrator.', 'danger')
                conversion.status = 'failed'
                db.session.add(APILog(
                    conversion_id=conversion.id,
                    type='error',
                    message="OpenAI API key format appears invalid"
                ))
                db.session.commit()
                return redirect(url_for('conversions'))
                
            # Start the conversion process
            logger.info(f"Starting conversion process for ID: {conversion.id}")
            process_conversion(conversion.id)
            logger.info(f"Conversion process initiated for ID: {conversion.id}")
            
            flash(f'Conversion "{title}" has been started!', 'success')
            return redirect(url_for('conversions'))
        except Exception as e:
            logger.error(f"Error starting conversion: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            flash(f'Error starting conversion: {str(e)}', 'danger')
            return redirect(url_for('convert'))
    
    return render_template('convert.html', title='Convert Text to Speech', form=form)


@app.route('/conversions')
@login_required
def conversions():
    user_conversions = Conversion.query.filter_by(user_id=current_user.id).order_by(Conversion.created_at.desc()).all()
    # Pass the format_seattle_time utility to the template
    return render_template('conversions.html', 
                          title='My Conversions', 
                          conversions=user_conversions,
                          format_seattle_time=format_seattle_time)


@app.route('/conversion/<uuid>/progress')
@login_required
def conversion_progress(uuid):
    try:
        logger.info(f"Fetching progress for conversion with UUID: {uuid}")
        conversion = Conversion.query.filter_by(uuid=uuid).first_or_404()
        
        # Check if the user has access to this conversion
        if conversion.user_id != current_user.id:
            logger.warning(f"Unauthorized access attempt for conversion {uuid} by user {current_user.id}")
            return jsonify({'error': 'Unauthorized'}), 403
        
        logger.info(f"Progress for conversion {uuid}: status={conversion.status}, progress={conversion.progress}")
        
        stale_after_minutes = app.config.get("STUCK_CONVERSION_MINUTES", 15)
        if conversion.status == 'processing':
            result = requeue_stale_processing_conversions(
                stale_after_minutes=stale_after_minutes,
                conversion_id=conversion.id,
            )
            if result["conversions"]:
                logger.warning(f"Conversion {uuid} was stale in processing state, requeued")
                db.session.refresh(conversion)

        if conversion.status == 'pending':
            restarted = claim_stale_pending_conversion_for_restart(
                conversion.id,
                stale_after_minutes=stale_after_minutes,
            )
            if restarted:
                logger.warning(f"Restarting stale pending conversion {uuid}")
                process_conversion(conversion.id)

                return jsonify({
                    'status': 'processing',
                    'progress': 0.0,
                    'message': 'Conversion was interrupted and is being restarted'
                })
        
        # Check if the file exists if status is completed
        if conversion.status == 'completed' and conversion.file_path:
            if not os.path.exists(conversion.file_path):
                logger.warning(f"File missing for completed conversion {uuid}, regenerating")
                # File is missing, mark for regeneration
                conversion.status = 'pending'
                conversion.progress = 0.0
                db.session.commit()
                
                # Restart the conversion process
                process_conversion(conversion.id)
                
                return jsonify({
                    'status': 'regenerating',
                    'progress': 0.0,
                    'message': 'File was missing and is being regenerated'
                })
        
        # Get recent logs for this conversion
        recent_logs = APILog.query.filter_by(conversion_id=conversion.id)\
            .order_by(APILog.timestamp.desc())\
            .limit(5)\
            .all()
        
        # Format logs for the response with Seattle time (GMT-8)
        logs = [{
            'type': log.type,
            'message': log.message,
            'timestamp': format_seattle_time(log.timestamp, '%Y-%m-%d %H:%M:%S')
        } for log in recent_logs]
        
        return jsonify({
            'status': conversion.status,
            'progress': conversion.progress,
            'updated_at': format_seattle_time(conversion.updated_at, '%Y-%m-%d %H:%M:%S'),
            'logs': logs
        })
    except Exception as e:
        logger.error(f"Error fetching progress for conversion {uuid}: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            'error': 'Error fetching progress',
            'message': str(e),
            'status': 'error',
            'progress': 0
        }), 500


@app.route('/conversion/<uuid>/download')
@login_required
def download_conversion(uuid):
    conversion = Conversion.query.filter_by(uuid=uuid).first_or_404()
    
    # Check if the user has access to this conversion
    if conversion.user_id != current_user.id:
        flash('You do not have permission to download this file', 'danger')
        return redirect(url_for('conversions'))
    
    # Check if the conversion is completed and file exists
    if conversion.status != 'completed' or not conversion.file_path:
        flash('This conversion is not ready for download yet', 'warning')
        return redirect(url_for('conversions'))
    
    # Check if the file exists
    if not os.path.exists(conversion.file_path):
        flash('File not found. Starting regeneration.', 'warning')
        conversion.status = 'pending'
        conversion.progress = 0.0
        db.session.commit()
        
        # Restart the conversion process
        process_conversion(conversion.id)
        
        return redirect(url_for('conversions'))
    
    # Send the file to the user
    return send_file(
        conversion.file_path,
        mimetype='audio/mpeg',
        as_attachment=True,
        download_name=f"{conversion.title.replace(' ', '_')}.mp3"
    )


@app.route('/conversion/<uuid>/cancel', methods=['POST'])
@login_required
def cancel_conversion_route(uuid):
    conversion = Conversion.query.filter_by(uuid=uuid).first_or_404()
    
    # Check if the user has access to this conversion
    if conversion.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Check if the conversion can be cancelled
    if conversion.status not in ['pending', 'processing']:
        return jsonify({'error': 'This conversion cannot be cancelled'}), 400
    
    # Cancel the conversion
    success = cancel_conversion(conversion.id)
    if success:
        return jsonify({'message': 'Conversion cancelled successfully'})
    else:
        return jsonify({'error': 'Failed to cancel conversion'}), 500


@app.route('/cleanup', methods=['POST'])
@login_required
def cleanup_files():
    """Clean up old files for the current user"""
    try:
        count = cleanup_old_files(current_user.id, keep_latest=50)
        return jsonify({'message': f'Successfully cleaned up {count} old files'})
    except Exception as e:
        logger.error(f"Error during file cleanup: {str(e)}")
        return jsonify({'error': f'Failed to clean up files: {str(e)}'}), 500


@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500


@app.route('/diagnostic/openai')
@diagnostics_required
def openai_diagnostic():
    """Admin-only diagnostic endpoint for OpenAI API."""
    try:
        from openai import OpenAI
        # Try to get API key from Flask config first, then fall back to environment
        api_key = app.config.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        
        if not api_key:
            return jsonify({
                'status': 'error',
                'message': 'OpenAI API key is missing from both Flask config and environment'
            }), 500
            
        # Create a client
        client = OpenAI(api_key=api_key)
        
        # Test TTS API specifically
        logger.info("OpenAI diagnostic: Testing OpenAI TTS API")
        tts_response = client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input="This is a diagnostic test of the OpenAI TTS API."
        )
        
        # Analyze TTS response
        tts_type = type(tts_response).__name__
        tts_attrs = dir(tts_response)
        logger.info(f"OpenAI diagnostic: TTS response type: {tts_type}")
        logger.info(f"OpenAI diagnostic: TTS response attributes: {tts_attrs}")
        
        # Try the write_to_file method
        temp_path = "/tmp/test_tts.mp3"
        if hasattr(tts_response, 'write_to_file'):
            tts_response.write_to_file(temp_path)
            file_size = os.path.getsize(temp_path) if os.path.exists(temp_path) else 0
            logger.info(f"OpenAI diagnostic: Successfully wrote file with size: {file_size} bytes")
        else:
            logger.warning("OpenAI diagnostic: write_to_file method not available")
        
        return jsonify({
            'status': 'success',
            'message': 'OpenAI API diagnostic completed',
            'tts_response_type': tts_type,
            'tts_methods': [attr for attr in tts_attrs if not attr.startswith('_')],
            'file_test': os.path.exists(temp_path)
        })
    except Exception as e:
        logger.error(f"OpenAI diagnostic error: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            'status': 'error',
            'message': f'Error in OpenAI diagnostic: {str(e)}'
        }), 500


@app.route('/diagnostic/conversion/<uuid>')
@diagnostics_required
def conversion_diagnostic(uuid):
    """Admin-only endpoint to get detailed conversion information."""
    try:
        conversion = Conversion.query.filter_by(uuid=uuid).first()
        if not conversion:
            return jsonify({'error': 'Conversion not found'}), 404
        
        # Get logs for this conversion
        logs = APILog.query.filter_by(conversion_id=conversion.id).order_by(APILog.timestamp.desc()).limit(20).all()
        log_data = [{
            'type': log.type,
            'message': log.message,
            'chunk_index': log.chunk_index,
            'status': log.status,
            'timestamp': format_seattle_time(log.timestamp)
        } for log in logs]
        
        # Get metrics if available, using the helper method to avoid issues with multiple metrics
        metrics_data = None
        metrics = conversion.get_latest_metrics()
        if metrics:
            metrics_data = {
                'chunking_time': metrics.chunking_time,
                'api_time': metrics.api_time,
                'combining_time': metrics.combining_time,
                'total_time': metrics.total_time,
                'chunk_count': metrics.chunk_count,
                'total_tokens': metrics.total_tokens
            }
        
        return jsonify({
            'conversion': {
                'id': conversion.id,
                'uuid': conversion.uuid,
                'user_id': conversion.user_id,
                'title': conversion.title,
                'text_length': len(conversion.text) if conversion.text else 0,
                'voice': conversion.voice,
                'tts_model': conversion.tts_model,
                'tts_model_label': conversion.tts_model_label,
                'status': conversion.status,
                'progress': conversion.progress,
                'file_path': conversion.file_path,
                'created_at': format_seattle_time(conversion.created_at),
                'updated_at': format_seattle_time(conversion.updated_at)
            },
            'metrics': metrics_data,
            'logs': log_data
        })
    except Exception as e:
        logger.error(f"Error getting conversion diagnostic for {uuid}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Error getting conversion diagnostic: {str(e)}'
        }), 500

@app.route('/diagnostic/conversion_logs/<uuid>')
@diagnostics_required
def conversion_logs_diagnostic(uuid):
    """Get detailed logs for a conversion"""
    try:
        conversion = Conversion.query.filter_by(uuid=uuid).first()
        if not conversion:
            return jsonify({'error': 'Conversion not found'}), 404
        
        # Get all logs for this conversion
        logs = APILog.query.filter_by(conversion_id=conversion.id).order_by(APILog.timestamp.desc()).all()
        log_data = [{
            'id': log.id,
            'type': log.type,
            'message': log.message,
            'chunk_index': log.chunk_index,
            'status': log.status,
            'timestamp': format_seattle_time(log.timestamp)
        } for log in logs]
        
        return jsonify({
            'uuid': uuid,
            'conversion_id': conversion.id,
            'status': conversion.status,
            'progress': conversion.progress,
            'logs_count': len(log_data),
            'logs': log_data
        })
    except Exception as e:
        logger.error(f"Error getting logs for conversion {uuid}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Error getting logs: {str(e)}'
        }), 500

@app.route('/diagnostic/all_conversions')
@diagnostics_required
def all_conversions_diagnostic():
    """Get status of all conversions"""
    try:
        conversions = Conversion.query.all()
        conversion_data = [{
            'id': conv.id,
            'uuid': conv.uuid,
            'title': conv.title,
            'status': conv.status,
            'progress': conv.progress,
            'voice': conv.voice,
            'tts_model': conv.tts_model,
            'tts_model_label': conv.tts_model_label,
            'created_at': format_seattle_time(conv.created_at),
            'updated_at': format_seattle_time(conv.updated_at)
        } for conv in conversions]
        
        return jsonify({
            'count': len(conversion_data),
            'conversions': conversion_data
        })
    except Exception as e:
        logger.error(f"Error getting all conversions: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Error getting all conversions: {str(e)}'
        }), 500

@app.route('/diagnostic/restart_conversion/<uuid>')
@diagnostics_required
def restart_conversion_diagnostic(uuid):
    """Admin-only endpoint to restart a conversion."""
    try:
        conversion = Conversion.query.filter_by(uuid=uuid).first()
        if not conversion:
            return jsonify({'error': 'Conversion not found'}), 404
        
        logger.info(f"Diagnostic: Restarting conversion {uuid}")
        conversion.status = 'pending'
        conversion.progress = 0.0
        conversion.updated_at = datetime.utcnow()
        db.session.commit()
        
        # Start the conversion process
        from tts_converter import process_conversion, cancel_conversion
        
        # First ensure any existing process is cancelled
        if conversion.status in ['processing', 'pending']:
            cancel_conversion(conversion.id)
            time.sleep(1)  # Give a moment for cancellation to be recognized
            
        # Start a fresh conversion
        process_conversion(conversion.id)
        
        return jsonify({
            'status': 'restarted',
            'message': f'Conversion {uuid} has been restarted',
            'conversion_id': conversion.id
        })
    except Exception as e:
        logger.error(f"Error restarting conversion {uuid}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Error restarting conversion: {str(e)}'
        }), 500

@app.route('/diagnostic/force_reset_conversion/<uuid>')
@diagnostics_required
def force_reset_conversion_diagnostic(uuid):
    """Forcefully reset a conversion that appears to be stuck"""
    try:
        conversion = Conversion.query.filter_by(uuid=uuid).first()
        if not conversion:
            return jsonify({'error': 'Conversion not found'}), 404
        
        logger.info(f"Diagnostic: Force resetting conversion {uuid}")
        
        # Import needed functions
        from tts_converter import cancel_conversion, cancellation_requests
        
        # First cancel any running conversion
        cancel_conversion(conversion.id)
        
        # Then ensure it's not in the cancellation list for the fresh start
        if conversion.id in cancellation_requests:
            logger.info(f"Removing conversion {uuid} from cancellation_requests dictionary")
            del cancellation_requests[conversion.id]
        
        # Delete existing logs for this conversion
        APILog.query.filter_by(conversion_id=conversion.id).delete()
        
        # Reset metrics - handle case of multiple metrics safely
        try:
            # Properly handle possible multiple metrics
            metrics_records = ConversionMetrics.query.filter_by(conversion_id=conversion.id).all()
            logger.info(f"Found {len(metrics_records)} metrics records for conversion {uuid}")
            for metric in metrics_records:
                db.session.delete(metric)
        except Exception as e:
            logger.error(f"Error deleting metrics: {str(e)}")
        
        # Reset conversion status
        conversion.status = 'pending'
        conversion.progress = 0.0
        conversion.updated_at = datetime.utcnow()
        conversion.file_path = None
        
        # Save changes
        db.session.commit()
        
        # Add a fresh log entry
        db.session.add(APILog(
            conversion_id=conversion.id,
            type='info',
            message=f"Conversion was force reset at {datetime.utcnow()}",
            status=200
        ))
        db.session.commit()
        
        # Create fresh metrics
        metrics = ConversionMetrics(
            conversion_id=conversion.id,
            chunk_count=len(re.split(r'[.!?]', conversion.text.strip())) // 15 + 1,  # Rough estimate
            total_tokens=len(conversion.text.split()) if conversion.text else 0
        )
        db.session.add(metrics)
        db.session.commit()
        
        # Start a fresh conversion
        from tts_converter import process_conversion
        process_conversion(conversion.id)
        
        return jsonify({
            'status': 'reset',
            'message': f'Conversion {uuid} has been forcefully reset',
            'conversion_id': conversion.id
        })
    except Exception as e:
        logger.error(f"Error force resetting conversion {uuid}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Error force resetting conversion: {str(e)}'
        }), 500

@app.route('/api_health_check')
@diagnostics_required
def api_health_check():
    """Admin-only OpenAI API health check."""
    try:
        from openai import OpenAI
        # Try to get API key from Flask config first, then fall back to environment
        api_key = app.config.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        
        if not api_key:
            return jsonify({
                'status': 'error',
                'message': 'OpenAI API key is missing from both Flask config and environment'
            }), 500
            
        # Create a client
        client = OpenAI(api_key=api_key)
        
        # Try a simple completion to test the API key
        logger.info("Testing OpenAI API key with a simple API call")
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=10
        )
        
        logger.info(f"OpenAI API test successful: {response.choices[0].message.content}")
        
        # Test TTS API specifically
        logger.info("Testing OpenAI TTS API")
        tts_response = client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input="This is a test of the OpenAI TTS API."
        )
        
        # Analyze TTS response
        tts_type = type(tts_response).__name__
        tts_attrs = dir(tts_response)
        logger.info(f"TTS response type: {tts_type}")
        logger.info(f"TTS response attributes: {tts_attrs}")
        
        # Try to read the content
        try:
            if hasattr(tts_response, 'read'):
                content = tts_response.read()
                logger.info(f"TTS content size: {len(content)} bytes")
            elif hasattr(tts_response, 'content'):
                content = tts_response.content
                logger.info(f"TTS content size: {len(content)} bytes")
            else:
                logger.warning("TTS response has no standard content attributes")
        except Exception as read_error:
            logger.error(f"Error reading TTS content: {str(read_error)}")
        
        # If we got here, the API key is working
        return jsonify({
            'status': 'success',
            'message': 'OpenAI API key is valid and working properly',
            'tts_response_type': tts_type
        })
    except Exception as e:
        logger.error(f"Error testing OpenAI API: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            'status': 'error',
            'message': f'Error testing OpenAI API: {str(e)}'
        }), 500
