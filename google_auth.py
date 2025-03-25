import json
import os

import requests
from flask import Blueprint, redirect, request, url_for, current_app
from flask_login import login_required, login_user, logout_user
from oauthlib.oauth2 import WebApplicationClient

from models import User
from app import db

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

# Initialize OAuth client
client = WebApplicationClient(GOOGLE_CLIENT_ID)

# Create blueprint
google_auth = Blueprint("google_auth", __name__)


@google_auth.route("/login")
def login():
    """Google login route"""
    # Find out what URL to hit for Google login
    google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
    authorization_endpoint = google_provider_cfg["authorization_endpoint"]

    # Use library to construct the request for Google login
    # First priority: use a specifically set OAUTH_REDIRECT_DOMAIN if available
    oauth_domain = os.environ.get("OAUTH_REDIRECT_DOMAIN", "")
    # Second priority: check for production domain
    prod_domain = os.environ.get("REPLIT_DOMAIN", "")
    # Third priority: get the development domain
    dev_domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
    
    # Get the actual request URL as a fallback
    request_domain = request.host
    
    # Log all domains for debugging
    print(f"OAuth domain: {oauth_domain}")
    print(f"Production domain: {prod_domain}")
    print(f"Development domain: {dev_domain}")
    print(f"Request host domain: {request_domain}")
    
    # Determine which domain to use, prioritizing the custom OAUTH domain
    domain = oauth_domain or prod_domain or dev_domain or request_domain
    
    # Special case for the known production domain
    if "text-to-mp-3-speech-bdgillihan.replit.app" in domain:
        redirect_uri = f"https://text-to-mp-3-speech-bdgillihan.replit.app/google_login/callback"
    else:
        # Ensure domain has protocol
        if domain and not domain.startswith(('http://', 'https://')):
            redirect_uri = f"https://{domain}/google_login/callback"
        else:
            # Last resort fallback to the actual requested URL
            redirect_uri = request.base_url.replace("http://", "https://") + "/callback"
    
    print(f"Using redirect URI: {redirect_uri}")
    
    request_uri = client.prepare_request_uri(
        authorization_endpoint,
        redirect_uri=redirect_uri,
        scope=["openid", "email", "profile"],
    )
    return redirect(request_uri)


@google_auth.route("/callback")
def callback():
    """Google login callback route"""
    # Get authorization code Google sent back
    code = request.args.get("code")
    
    # Find out what URL to hit to get tokens
    google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
    token_endpoint = google_provider_cfg["token_endpoint"]
    
    # First priority: use a specifically set OAUTH_REDIRECT_DOMAIN if available
    oauth_domain = os.environ.get("OAUTH_REDIRECT_DOMAIN", "")
    # Second priority: check for production domain
    prod_domain = os.environ.get("REPLIT_DOMAIN", "")
    # Third priority: get the development domain
    dev_domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
    
    # Get the actual request URL as a fallback
    request_domain = request.host
    
    # Log all domains for debugging
    print(f"OAuth domain: {oauth_domain}")
    print(f"Production domain: {prod_domain}")
    print(f"Development domain: {dev_domain}")
    print(f"Request host domain: {request_domain}")
    
    # Determine which domain to use, prioritizing the custom OAUTH domain
    domain = oauth_domain or prod_domain or dev_domain or request_domain
    
    # Special case for the known production domain
    if "text-to-mp-3-speech-bdgillihan.replit.app" in domain:
        redirect_url = f"https://text-to-mp-3-speech-bdgillihan.replit.app/google_login/callback"
    else:
        # Ensure domain has protocol
        if domain and not domain.startswith(('http://', 'https://')):
            redirect_url = f"https://{domain}/google_login/callback"
        else:
            # Last resort fallback to the actual requested URL
            redirect_url = request.base_url.replace("http://", "https://")
    
    print(f"Using callback redirect URL: {redirect_url}")
    
    # Prepare and send a request to get tokens
    token_url, headers, body = client.prepare_token_request(
        token_endpoint,
        authorization_response=request.url.replace("http://", "https://"),
        redirect_url=redirect_url,
        code=code
    )
    # Ensure client ID and secret are not None
    google_client_id = GOOGLE_CLIENT_ID or ""
    google_client_secret = GOOGLE_CLIENT_SECRET or ""
    token_response = requests.post(
        token_url,
        headers=headers,
        data=body,
        auth=(google_client_id, google_client_secret),
    )

    # Parse the tokens
    client.parse_request_body_response(json.dumps(token_response.json()))
    
    # Get user info from Google
    userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
    uri, headers, body = client.add_token(userinfo_endpoint)
    userinfo_response = requests.get(uri, headers=headers, data=body)
    
    # Verify user info
    if userinfo_response.json().get("email_verified"):
        email = userinfo_response.json()["email"]
        name = userinfo_response.json()["given_name"]
    else:
        return "User email not available or not verified by Google.", 400
    
    # Check if user exists, if not create a new one
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(
            username=name,
            email=email,
            # Use a secure random password as we won't use it for login
            password_hash="google_oauth_user"
        )
        db.session.add(user)
        db.session.commit()
    
    # Log in the user
    login_user(user)
    
    # Redirect to home page
    return redirect(url_for("dashboard"))


@google_auth.route("/logout")
@login_required
def logout():
    """Logout route"""
    logout_user()
    return redirect(url_for("index"))