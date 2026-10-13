"""
Auth Blueprint
--------------
POST /auth/login   — obtain session cookie
POST /auth/logout  — clear session
POST /auth/register — create user (Admin only, or first-run)
"""
from flask import Blueprint, request, jsonify, session
from flask_login import login_user, logout_user, login_required, current_user
from app import db, bcrypt, limiter
from app.models import User
from app.services.audit import log_audit_event

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.post("/register")
def register():
    """
    Create a new user account.
    First user is automatically admin; subsequent users require an existing admin session.
    """
    data = request.get_json(force=True)
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role = data.get("role", "viewer")

    if not username or not password:
        return jsonify({"error": "username and password required"}), 400

    # Allow first-user bootstrap; after that, only admins may register new users
    user_count = User.query.count()
    if user_count > 0:
        if not current_user.is_authenticated or current_user.role != "admin":
            return jsonify({"error": "Admin access required"}), 403

    if User.query.filter_by(username=username).first():
        return jsonify({"error": "Username already taken"}), 409

    hashed = bcrypt.generate_password_hash(password).decode("utf-8")
    user = User(username=username, password_hash=hashed,
                role="admin" if user_count == 0 else role)
    db.session.add(user)
    db.session.commit()
    log_audit_event(
        action="USER_REGISTER",
        target_type="user",
        target_id=user.id,
        details=f"username={user.username} role={user.role}",
    )
    return jsonify(user.to_dict()), 201


@auth_bp.post("/login")
@limiter.limit("15 per minute")
def login():
    """Authenticate and start a session."""
    data = request.get_json(force=True)
    username = data.get("username")
    user = User.query.filter_by(username=username).first()
    if not user or not bcrypt.check_password_hash(user.password_hash, data.get("password", "")):
        log_audit_event(
            action="LOGIN_FAILURE",
            target_type="user",
            target_id=username,
            details="Invalid username or password",
        )
        return jsonify({"error": "Invalid credentials"}), 401
    login_user(user)
    session.permanent = True
    log_audit_event(
        action="LOGIN_SUCCESS",
        target_type="user",
        target_id=user.id,
        user_id=user.id,
        username=user.username,
        role=user.role,
    )
    return jsonify(user.to_dict())


@auth_bp.post("/logout")
@login_required
def logout():
    """End the current session."""
    log_audit_event(
        action="LOGOUT",
        target_type="user",
        target_id=current_user.id,
        user_id=current_user.id,
        username=current_user.username,
        role=current_user.role,
    )
    logout_user()
    return jsonify({"message": "Logged out"})


@auth_bp.get("/me")
@login_required
def me():
    """Return the currently authenticated user."""
    return jsonify(current_user.to_dict())
