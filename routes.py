import os
import logging
import traceback
from datetime import datetime
from flask import render_template, flash, redirect, url_for, request, jsonify, send_file
from flask_login import login_user, logout_user, current_user, login_required
from urllib.parse import urlparse

from app import app, db
from forms import LoginForm, RegistrationForm, ConversionForm
from models import User, Conversion, ConversionMetrics, APILog
from tts_converter import process_conversion, cancel_conversion
from utils import cleanup_old_files

logger = logging.getLogger(__name__)

@app.route('/')
def index():
    # Check and log the OpenAI API key status on app startup for debugging
    api_key = app.config.get("OPENAI_API_KEY")
    logger.info(f"OpenAI API key status: {'Available and valid format' if api_key and api_key.startswith('sk-') else 'Missing or invalid format'}")
    
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html', title='Text to Speech Converter')


@app.route('/login')
def login():
    """Redirect to Google login"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('google_auth.login'))


@app.route('/register')
def register():
    """Redirect to Google login for registration"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('google_auth.login'))


@app.route('/logout')
def logout():
    """Redirect to Google logout"""
    return redirect(url_for('google_auth.logout'))


@app.route('/dashboard')
@login_required
def dashboard():
    recent_conversions = Conversion.query.filter_by(user_id=current_user.id).order_by(Conversion.created_at.desc()).limit(5).all()
    return render_template('dashboard.html', title='Dashboard', conversions=recent_conversions)


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
    return render_template('conversions.html', title='My Conversions', conversions=user_conversions)


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
        
        # Format logs for the response
        logs = [{
            'type': log.type,
            'message': log.message,
            'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        } for log in recent_logs]
        
        return jsonify({
            'status': conversion.status,
            'progress': conversion.progress,
            'updated_at': conversion.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
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


@app.route('/api_health_check')
@login_required
def api_health_check():
    """Check if the OpenAI API is working properly (authenticated)"""
    try:
        from openai import OpenAI
        api_key = app.config.get("OPENAI_API_KEY")
        
        if not api_key:
            return jsonify({
                'status': 'error',
                'message': 'OpenAI API key is missing'
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
        
        # If we got here, the API key is working
        return jsonify({
            'status': 'success',
            'message': 'OpenAI API key is valid and working properly'
        })
    except Exception as e:
        logger.error(f"Error testing OpenAI API: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            'status': 'error',
            'message': f'Error testing OpenAI API: {str(e)}'
        }), 500
