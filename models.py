from datetime import datetime
import uuid
from app import db, login_manager
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    conversions = db.relationship('Conversion', backref='user', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'


class Conversion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(256), nullable=False)
    text = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, processing, completed, failed, cancelled
    progress = db.Column(db.Float, default=0.0)  # 0-100%
    uuid = db.Column(db.String(36), default=lambda: str(uuid.uuid4()), unique=True)
    file_path = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    metrics = db.relationship('ConversionMetrics', backref='conversion', uselist=False, cascade="all, delete-orphan")
    logs = db.relationship('APILog', backref='conversion', lazy='dynamic', cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Conversion {self.title}>'


class ConversionMetrics(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversion_id = db.Column(db.Integer, db.ForeignKey('conversion.id'), nullable=False)
    chunking_time = db.Column(db.Float, nullable=True)  # in seconds
    api_time = db.Column(db.Float, nullable=True)  # in seconds
    combining_time = db.Column(db.Float, nullable=True)  # in seconds
    total_time = db.Column(db.Float, nullable=True)  # in seconds
    chunk_count = db.Column(db.Integer, nullable=True)
    total_tokens = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<ConversionMetrics for Conversion {self.conversion_id}>'


class APILog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversion_id = db.Column(db.Integer, db.ForeignKey('conversion.id'), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # error, warning, info
    status = db.Column(db.Integer, nullable=True)
    message = db.Column(db.Text, nullable=False)
    chunk_index = db.Column(db.Integer, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<APILog {self.type}: {self.message[:30]}...>'
