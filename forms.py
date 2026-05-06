from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, TextAreaField, SelectField
from wtforms.validators import DataRequired, Email, EqualTo, ValidationError, Length
from models import (
    DEFAULT_CONVERSION_RETENTION_DAYS,
    RETENTION_POLICY_CHOICES,
    TTS_MODEL_CHOICES,
    TTS_MODEL_FAST,
    User,
)


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')


class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=64)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=8)])
    password2 = PasswordField('Repeat Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Register')

    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user is not None:
            raise ValidationError('Please use a different username.')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user is not None:
            raise ValidationError('Please use a different email address.')


class ConversionForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired(), Length(max=256)])
    text = TextAreaField('Text to convert', validators=[
        DataRequired(), 
        Length(max=100000, message="Text must be no more than 100,000 characters.")
    ])
    voice = SelectField('Voice', validators=[DataRequired()], 
                       choices=[
                           ('onyx', 'Onyx - Professional and versatile (Default)'),
                           ('alloy', 'Alloy - Versatile, neutral gender voice'),
                           ('echo', 'Echo - Deeper, announcer-like voice'),
                           ('fable', 'Fable - Smooth narrative voice'),
                           ('nova', 'Nova - Warm and pleasant'),
                           ('shimmer', 'Shimmer - Clear, bright, and engaging')
                       ], default='onyx')
    tts_model = SelectField(
        'Conversion Mode',
        validators=[DataRequired()],
        choices=TTS_MODEL_CHOICES,
        default=TTS_MODEL_FAST,
    )
    retention_policy = SelectField(
        'Storage',
        validators=[DataRequired()],
        choices=RETENTION_POLICY_CHOICES,
        default=str(DEFAULT_CONVERSION_RETENTION_DAYS),
    )
    submit = SubmitField('Convert to Speech')
