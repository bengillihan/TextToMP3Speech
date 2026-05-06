import os
import logging
from urllib.parse import urlparse
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from sqlalchemy.orm import DeclarativeBase


def _env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_domain(value):
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    return (parsed.netloc or parsed.path).strip("/")


# Configure logging
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO))
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
app.config["OAUTH_REDIRECT_DOMAIN"] = _normalize_domain(
    os.environ.get("OAUTH_REDIRECT_DOMAIN")
    or os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    or os.environ.get("REPLIT_DOMAIN")
)
app.config["DIAGNOSTICS_ENABLED"] = _env_flag("DIAGNOSTICS_ENABLED", False)
app.config["DIAGNOSTIC_ADMIN_EMAILS"] = {
    email.strip().lower()
    for email in os.environ.get("DIAGNOSTIC_ADMIN_EMAILS", "").split(",")
    if email.strip()
}
auto_create_default = not bool(os.environ.get("RAILWAY_ENVIRONMENT"))
app.config["AUTO_CREATE_TABLES"] = _env_flag("AUTO_CREATE_TABLES", auto_create_default)

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
app.config["CONVERSION_RETENTION_DAYS"] = int(os.environ.get("CONVERSION_RETENTION_DAYS", "90"))

# Configure OpenAI
app.config["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY")

# Initialize the extensions with the app
db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'google_auth.login'

if app.config["OAUTH_REDIRECT_DOMAIN"]:
    logger.info(
        "Google OAuth redirect URI: https://%s/google_login/callback",
        app.config["OAUTH_REDIRECT_DOMAIN"],
    )
else:
    logger.info("Set OAUTH_REDIRECT_DOMAIN to force a stable Google OAuth callback domain.")

# Import models (must be imported after db initialization)
with app.app_context():
    # Import models and routes
    from models import User, Conversion, ConversionMetrics, APILog
    import routes
    from google_auth import google_auth
    
    # Register blueprints
    app.register_blueprint(google_auth, url_prefix='/google_login')
    
    if app.config["AUTO_CREATE_TABLES"]:
        db.create_all()
        logger.info("Database tables created")
    else:
        logger.info("Skipping db.create_all because AUTO_CREATE_TABLES is disabled")
