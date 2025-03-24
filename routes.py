import os
import logging
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
        # Create new conversion record
        conversion = Conversion(
            user_id=current_user.id,
            title=form.title.data,
            text=form.text.data,
            status='pending',
            progress=0.0
        )
        db.session.add(conversion)
        db.session.commit()
        
        # Start the conversion process
        process_conversion(conversion.id)
        
        flash(f'Conversion "{form.title.data}" has been started!', 'success')
        return redirect(url_for('conversions'))
    
    return render_template('convert.html', title='Convert Text to Speech', form=form)


@app.route('/conversions')
@login_required
def conversions():
    user_conversions = Conversion.query.filter_by(user_id=current_user.id).order_by(Conversion.created_at.desc()).all()
    return render_template('conversions.html', title='My Conversions', conversions=user_conversions)


@app.route('/conversion/<uuid>/progress')
@login_required
def conversion_progress(uuid):
    conversion = Conversion.query.filter_by(uuid=uuid).first_or_404()
    
    # Check if the user has access to this conversion
    if conversion.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Check if the file exists if status is completed
    if conversion.status == 'completed' and conversion.file_path:
        if not os.path.exists(conversion.file_path):
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
    
    return jsonify({
        'status': conversion.status,
        'progress': conversion.progress,
        'updated_at': conversion.updated_at.strftime('%Y-%m-%d %H:%M:%S')
    })


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
