import os
import logging
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from sqlalchemy.orm import DeclarativeBase

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Create database base class
class Base(DeclarativeBase):
    pass

# Initialize extensions
db = SQLAlchemy(model_class=Base)
login_manager = LoginManager()

# Create the app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")

# Configure the database
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Configure file storage
app.config["AUDIO_STORAGE_PATH"] = os.path.expanduser("~/persistent_audio_files")
os.makedirs(app.config["AUDIO_STORAGE_PATH"], exist_ok=True)

# Configure OpenAI
app.config["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY")

# Initialize the extensions with the app
db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'google_auth.login'

# Display the Google OAuth redirect URIs that need to be registered
# Check for all possible domains
oauth_domain = os.environ.get("OAUTH_REDIRECT_DOMAIN", "")
prod_domain = os.environ.get("REPLIT_DOMAIN", "")
dev_domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
hardcoded_domain = "text-to-mp-3-speech-bdgillihan.replit.app"

# For the current domain issue in the error
error_domain = "284699a3-738e-48f9-8868-5f261bc94a86-00-uw70cafxtgnu.worf.replit.dev"

logger.info("="*80)
logger.info(f"IMPORTANT: Register these OAuth redirect URIs in Google Cloud Console:")

if oauth_domain:
    oauth_redirect_uri = f"https://{oauth_domain}/google_login/callback"
    logger.info(f"Custom OAuth URI: {oauth_redirect_uri}")

if prod_domain:
    prod_redirect_uri = f"https://{prod_domain}/google_login/callback"
    logger.info(f"Production URI: {prod_redirect_uri}")

if dev_domain:
    dev_redirect_uri = f"https://{dev_domain}/google_login/callback"
    logger.info(f"Development URI: {dev_redirect_uri}")

# Add the hardcoded production domain
hardcoded_redirect_uri = f"https://{hardcoded_domain}/google_login/callback"
logger.info(f"Hardcoded Production URI: {hardcoded_redirect_uri}")

# Add the error domain from the error message
error_redirect_uri = f"https://{error_domain}/google_login/callback"
logger.info(f"Error Domain URI: {error_redirect_uri}")

logger.info("="*80)
logger.info("IMPORTANT: Make sure to add ALL these URIs to your Google OAuth consent screen")
logger.info("="*80)

# Import models (must be imported after db initialization)
with app.app_context():
    # Import models and routes
    from models import User, Conversion, ConversionMetrics, APILog
    import routes
    from google_auth import google_auth
    
    # Register blueprints
    app.register_blueprint(google_auth, url_prefix='/google_login')
    
    # Create database tables
    db.create_all()
    
    logger.info("Database tables created")
