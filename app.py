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
login_manager.login_view = 'login'

# Import models (must be imported after db initialization)
with app.app_context():
    # Import models and routes
    from models import User, Conversion, ConversionMetrics, APILog
    import routes
    
    # Create database tables
    db.create_all()
    
    logger.info("Database tables created")
