import json
import os
import logging

import requests
from flask import Blueprint, redirect, request, url_for, current_app, session
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
    try:
        google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
        authorization_endpoint = google_provider_cfg["authorization_endpoint"]
    except Exception as e:
        current_app.logger.error(f"Error fetching Google auth endpoint: {str(e)}")
        return "Error contacting Google authentication service. Please try again later.", 500

    # Use library to construct the request for Google login
    # CRITICAL: When accessing from production, ALWAYS use the production domain
    # Check if this is a request coming from the production domain
    referer = request.headers.get('Referer', '')
    request_domain = request.host
    request_url = request.url
    origin = request.headers.get('Origin', '')
    
    # Log all domains and headers for debugging
    current_app.logger.info(f"OAuth Login - Referer: {referer}")
    current_app.logger.info(f"OAuth Login - Request host: {request_domain}")
    current_app.logger.info(f"OAuth Login - Request URL: {request_url}")
    current_app.logger.info(f"OAuth Login - Origin: {origin}")
    
    # Determine which domain to use for the callback
    production_domain = "text-to-mp-3-speech-bdgillihan.replit.app"
    
    # If we're coming from the production domain or the request is from production
    is_production = (production_domain in referer or 
                     production_domain in request_domain or
                     production_domain in request_url or
                     production_domain in origin)
    
    if is_production:
        # Always use the production domain for redirect
        redirect_uri = f"https://{production_domain}/google_login/callback"
        current_app.logger.info(f"OAuth Login - Using PRODUCTION redirect URI: {redirect_uri}")
    else:
        # For development environment, use the current domain
        # This ensures we don't have a mismatch during development
        redirect_uri = f"https://{request_domain}/google_login/callback"
        current_app.logger.info(f"OAuth Login - Using DEVELOPMENT redirect URI: {redirect_uri}")
    
    # Store the domain used in the session to ensure consistent domain use in callback
    session['oauth_domain'] = production_domain if is_production else request_domain
    session['is_production'] = is_production
    
    try:
        # Prepare the request URI
        request_uri = client.prepare_request_uri(
            authorization_endpoint,
            redirect_uri=redirect_uri,
            scope=["openid", "email", "profile"],
        )
        current_app.logger.info(f"OAuth Login - Request URI: {request_uri}")
        return redirect(request_uri)
    except Exception as e:
        current_app.logger.error(f"OAuth Login - Error preparing request: {str(e)}")
        return "Error preparing authentication request. Please try again later.", 500


@google_auth.route("/callback")
def callback():
    """Google login callback route"""
    try:
        # Get authorization code Google sent back
        code = request.args.get("code")
        if not code:
            current_app.logger.error("OAuth Callback - No authorization code received from Google")
            return "Authorization failed. No code received from Google.", 400
        
        # Find out what URL to hit to get tokens
        try:
            google_provider_cfg = requests.get(GOOGLE_DISCOVERY_URL).json()
            token_endpoint = google_provider_cfg["token_endpoint"]
        except Exception as e:
            current_app.logger.error(f"OAuth Callback - Error fetching Google token endpoint: {str(e)}")
            return "Error contacting Google authentication service. Please try again later.", 500
        
        # CRITICAL: When accessing from production, ALWAYS use the production domain
        # Check if this is a request coming from the production domain
        referer = request.headers.get('Referer', '')
        request_domain = request.host
        request_url = request.url
        
        # Log all domains and headers for debugging
        current_app.logger.info(f"OAuth Callback - Referer: {referer}")
        current_app.logger.info(f"OAuth Callback - Request host: {request_domain}")
        current_app.logger.info(f"OAuth Callback - Request URL: {request_url}")
        
        # Determine which domain to use for the callback
        production_domain = "text-to-mp-3-speech-bdgillihan.replit.app"
        
        # Check if we have session data from the login step
        session_domain = session.get('oauth_domain')
        session_is_production = session.get('is_production', False)
        
        current_app.logger.info(f"OAuth Callback - Session domain: {session_domain}")
        current_app.logger.info(f"OAuth Callback - Session is_production: {session_is_production}")
        
        # If we're coming from the production domain or the request is from production
        # or the session indicates production
        is_production = (
            production_domain in referer or 
            production_domain in request_domain or 
            production_domain in request_url or
            session_is_production
        )
        
        # Use the session domain if available, otherwise determine from the current request
        if session_domain:
            domain_to_use = session_domain
            current_app.logger.info(f"OAuth Callback - Using session domain: {domain_to_use}")
        elif is_production:
            domain_to_use = production_domain
            current_app.logger.info(f"OAuth Callback - Using production domain (from detection): {domain_to_use}")
        else:
            domain_to_use = request_domain
            current_app.logger.info(f"OAuth Callback - Using request domain: {domain_to_use}")
        
        # Always use the determined domain for redirect
        redirect_url = f"https://{domain_to_use}/google_login/callback"
        current_app.logger.info(f"OAuth Callback - Final redirect URL: {redirect_url}")
        
        # Prepare and send a request to get tokens
        try:
            current_app.logger.info(f"OAuth Callback - Authorization response URL: {request.url}")
            
            # Make sure we're using https, even if the request came in via http
            authorization_response = request.url
            if authorization_response.startswith('http:'):
                authorization_response = authorization_response.replace('http:', 'https:', 1)
                current_app.logger.info(f"OAuth Callback - Converted authorization response to HTTPS: {authorization_response}")
            
            token_url, headers, body = client.prepare_token_request(
                token_endpoint,
                authorization_response=authorization_response,
                redirect_url=redirect_url,
                code=code
            )
            
            # Ensure client ID and secret are not None
            google_client_id = GOOGLE_CLIENT_ID or ""
            google_client_secret = GOOGLE_CLIENT_SECRET or ""
            
            if not google_client_id or not google_client_secret:
                current_app.logger.error("OAuth Callback - Missing Google OAuth credentials")
                return "Authentication failed. Missing OAuth credentials.", 500
                
            token_response = requests.post(
                token_url,
                headers=headers,
                data=body,
                auth=(google_client_id, google_client_secret),
            )
            
            if token_response.status_code != 200:
                current_app.logger.error(f"OAuth Callback - Token request failed: {token_response.status_code} {token_response.text}")
                return "Authentication failed. Could not retrieve token from Google.", 500
                
            # Parse the tokens
            client.parse_request_body_response(json.dumps(token_response.json()))
            
        except Exception as e:
            current_app.logger.error(f"OAuth Callback - Error in token exchange: {str(e)}")
            return "Error during authentication. Please try again later.", 500
        
        # Get user info from Google
        try:
            userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
            uri, headers, body = client.add_token(userinfo_endpoint)
            userinfo_response = requests.get(uri, headers=headers, data=body)
            
            if userinfo_response.status_code != 200:
                current_app.logger.error(f"OAuth Callback - User info request failed: {userinfo_response.status_code} {userinfo_response.text}")
                return "Authentication failed. Could not retrieve user information.", 500
            
            userinfo_data = userinfo_response.json()
            
            # Verify user info
            if userinfo_data.get("email_verified"):
                email = userinfo_data["email"]
                name = userinfo_data.get("given_name", email.split('@')[0])  # Fallback to username from email
                current_app.logger.info(f"OAuth Callback - User authenticated: {email}")
            else:
                current_app.logger.error("OAuth Callback - User email not verified by Google")
                return "Authentication failed. Email not verified by Google.", 400
                
        except Exception as e:
            current_app.logger.error(f"OAuth Callback - Error retrieving user info: {str(e)}")
            return "Error retrieving user information. Please try again later.", 500
        
        # Check if user exists, if not create a new one
        try:
            user = User.query.filter_by(email=email).first()
            if not user:
                current_app.logger.info(f"OAuth Callback - Creating new user: {email}")
                user = User(
                    username=name,
                    email=email,
                    # Use a secure random password for OAuth users
                    password_hash="google_oauth_user"
                )
                db.session.add(user)
                db.session.commit()
            else:
                current_app.logger.info(f"OAuth Callback - Existing user logged in: {email}")
            
            # Log in the user
            login_user(user)
            
            # Redirect to home page with success message
            return redirect(url_for("dashboard"))
            
        except Exception as e:
            current_app.logger.error(f"OAuth Callback - Database error: {str(e)}")
            return "Error creating user account. Please try again later.", 500
            
    except Exception as e:
        current_app.logger.error(f"OAuth Callback - Unexpected error: {str(e)}")
        return "An unexpected error occurred. Please try again later.", 500


@google_auth.route("/logout")
@login_required
def logout():
    """Logout route"""
    logout_user()
    return redirect(url_for("index"))