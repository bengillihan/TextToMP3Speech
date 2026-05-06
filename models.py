from datetime import datetime
import uuid
from app import db, login_manager
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

TTS_MODEL_FAST = 'tts-1'
TTS_MODEL_QUALITY = 'gpt-4o-mini-tts'
TTS_MODEL_LABELS = {
    TTS_MODEL_FAST: 'Fast',
    TTS_MODEL_QUALITY: 'Quality',
}
TTS_MODEL_CHOICES = [
    (TTS_MODEL_FAST, 'Fast - lower latency'),
    (TTS_MODEL_QUALITY, 'Quality - better voice and control'),
]

DEFAULT_CONVERSION_RETENTION_DAYS = 90
RETENTION_KEEP = 'keep'
RETENTION_DAY_CHOICES = (7, 30, 90)
RETENTION_POLICY_CHOICES = [
    ('7', 'Auto-delete after 7 days'),
    ('30', 'Auto-delete after 30 days'),
    ('90', 'Auto-delete after 90 days'),
    (RETENTION_KEEP, 'Keep until I delete it'),
]


def normalize_tts_model(tts_model):
    if tts_model in TTS_MODEL_LABELS:
        return tts_model
    return TTS_MODEL_FAST


def retention_settings_from_policy(policy_value, default_days=DEFAULT_CONVERSION_RETENTION_DAYS):
    if policy_value == RETENTION_KEEP:
        return True, default_days

    try:
        retention_days = int(policy_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid retention policy") from exc

    if retention_days not in RETENTION_DAY_CHOICES:
        raise ValueError("Invalid retention policy")

    return False, retention_days


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)  # Nullable for Google OAuth users
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    conversions = db.relationship('Conversion', backref='user', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'


class Conversion(db.Model):
    __table_args__ = (
        db.Index('ix_conversion_user_id', 'user_id'),
        db.Index('ix_conversion_created_at', 'created_at'),
        db.Index('ix_conversion_status', 'status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(256), nullable=False)
    text = db.Column(db.Text, nullable=False)
    voice = db.Column(db.String(20), default='onyx')  # Store the selected voice
    tts_model = db.Column(db.String(64), nullable=False, default=TTS_MODEL_FAST)
    keep_forever = db.Column(db.Boolean, nullable=False, default=False)
    retention_days = db.Column(db.Integer, nullable=False, default=DEFAULT_CONVERSION_RETENTION_DAYS)
    status = db.Column(db.String(20), default='pending')  # pending, processing, completed, failed, cancelled
    progress = db.Column(db.Float, default=0.0)  # 0-100%
    uuid = db.Column(db.String(36), default=lambda: str(uuid.uuid4()), unique=True)
    file_path = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Define metrics relationship with simple one-to-many
    metrics = db.relationship('ConversionMetrics', backref='conversion', uselist=False,
                             cascade="all, delete-orphan")
                             
    def get_latest_metrics(self):
        """Helper to get the most recent metrics entry if multiple exist"""
        from sqlalchemy import desc
        # Query all metrics for this conversion and get the latest one
        return ConversionMetrics.query.filter_by(conversion_id=self.id).order_by(
            desc(ConversionMetrics.id)).first()
    logs = db.relationship('APILog', backref='conversion', lazy='dynamic', cascade="all, delete-orphan")

    @property
    def tts_model_label(self):
        return TTS_MODEL_LABELS.get(normalize_tts_model(self.tts_model), 'Fast')

    @property
    def retention_policy_value(self):
        if self.keep_forever:
            return RETENTION_KEEP
        return str(self.retention_days or DEFAULT_CONVERSION_RETENTION_DAYS)

    @property
    def retention_label(self):
        if self.keep_forever:
            return 'Keep'
        retention_days = self.retention_days or DEFAULT_CONVERSION_RETENTION_DAYS
        return f'Auto-delete after {retention_days} days'

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
    __table_args__ = (
        db.Index('ix_api_log_conversion_id', 'conversion_id'),
        db.Index('ix_api_log_timestamp', 'timestamp'),
    )

    id = db.Column(db.Integer, primary_key=True)
    conversion_id = db.Column(db.Integer, db.ForeignKey('conversion.id'), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # error, warning, info
    status = db.Column(db.Integer, nullable=True)
    message = db.Column(db.Text, nullable=False)
    chunk_index = db.Column(db.Integer, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<APILog {self.type}: {self.message[:30]}...>'
