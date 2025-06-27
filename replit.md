# Text-to-Speech Converter Application

## Overview

This is a Flask-based web application that converts text to speech using OpenAI's TTS (Text-to-Speech) API. The application features user authentication via Google OAuth, supports large text inputs through automatic chunking, and provides parallel processing for optimal performance. Users can convert up to 100,000 characters of text into high-quality MP3 audio files using various voice options.

## System Architecture

### Frontend Architecture
- **Framework**: Bootstrap with dark Replit theme for responsive UI
- **Templates**: Jinja2 templating engine with modular base template
- **JavaScript**: Vanilla JS for progress polling and real-time conversion updates
- **Styling**: Custom CSS with status-specific color schemes and responsive design

### Backend Architecture
- **Framework**: Flask with SQLAlchemy ORM
- **Authentication**: Flask-Login with Google OAuth 2.0 integration
- **Database**: PostgreSQL with SQLAlchemy models
- **File Storage**: Persistent local storage for audio files
- **API Integration**: OpenAI's TTS-1 model for speech synthesis

### Deployment Strategy
- **Platform**: Replit with autoscale deployment target
- **Server**: Gunicorn WSGI server with reload capabilities
- **Dependencies**: Python 3.11 with Nix package management
- **External Dependencies**: FFmpeg for audio processing, PostgreSQL for data persistence

## Key Components

### User Management
- **Models**: User model with Google OAuth support and traditional password authentication
- **Authentication**: Google OAuth 2.0 with automatic user creation
- **Sessions**: Flask-Login session management with secure cookie handling

### Text-to-Speech Processing
- **Chunking Logic**: Automatic text splitting for large inputs (max 100,000 characters)
- **Parallel Processing**: Asynchronous processing of multiple text chunks
- **Voice Options**: Six voice choices (onyx, alloy, echo, fable, nova, shimmer)
- **Progress Tracking**: Real-time progress updates with database persistence

### File Management
- **Storage**: Persistent audio file storage in user-specific directories
- **Cleanup**: Automatic cleanup of old files to manage storage space
- **Download**: Secure file serving with download tracking

### Data Models
- **User**: Authentication and profile information
- **Conversion**: Text conversion requests with status tracking
- **ConversionMetrics**: Performance metrics and processing details
- **APILog**: OpenAI API usage tracking and error logging

## Data Flow

1. **User Authentication**: Google OAuth flow creates or authenticates users
2. **Text Submission**: Users submit text through web forms with validation
3. **Text Processing**: Large text is automatically chunked into manageable segments
4. **TTS Generation**: Parallel API calls to OpenAI for each text chunk
5. **Audio Combination**: Individual audio files are merged using PyDub/FFmpeg
6. **File Storage**: Final MP3 files are stored in persistent directories
7. **Progress Updates**: Real-time status updates via AJAX polling
8. **Download Delivery**: Secure file serving with usage tracking

## External Dependencies

### Core Services
- **OpenAI API**: TTS-1 model for speech synthesis
- **Google OAuth**: Authentication service integration
- **PostgreSQL**: Primary database for application data

### System Dependencies
- **FFmpeg**: Audio processing and format conversion
- **PyDub**: Python audio manipulation library
- **Gunicorn**: Production WSGI server

### Python Packages
- **Flask Ecosystem**: Flask, Flask-SQLAlchemy, Flask-Login, Flask-WTF
- **OpenAI SDK**: Async and sync clients for API integration
- **Audio Processing**: PyDub for audio file manipulation
- **Authentication**: OAuthLib for OAuth 2.0 flows

## Deployment Strategy

### Development Environment
- **Replit Integration**: Configured for Replit's development environment
- **Live Reload**: Automatic server restart on file changes
- **Debug Mode**: Comprehensive logging and error reporting

### Production Configuration
- **Autoscale Deployment**: Replit's autoscale target for production workloads
- **Environment Variables**: Secure configuration for API keys and database URLs
- **Health Monitoring**: Application health checks and error handling

### Database Management
- **Migration Strategy**: Manual schema updates (recommended to add Flask-Migrate)
- **Connection Pooling**: SQLAlchemy connection pool with pre-ping health checks
- **Data Persistence**: Persistent storage for user data and conversion history

## Changelog
- June 27, 2025. Initial setup
- June 27, 2025. Added auto-shutdown functionality to main.py for resource management when server is idle

## User Preferences

Preferred communication style: Simple, everyday language.