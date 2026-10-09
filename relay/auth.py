"""Session auth: register / login / logout."""
from __future__ import annotations

from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import store

bp = Blueprint("auth", __name__)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "uid" not in session:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def current_user(app):
    uid = session.get("uid")
    if not uid:
        return None
    return store.get_user(app, uid)


@bp.route("/register", methods=["GET", "POST"])
def register():
    from flask import current_app
    if "uid" in session:
        return redirect(url_for("dash.overview"))
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or "@" not in email:
            error = "Please enter your name and a valid email."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif store.get_user_by_email(current_app, email):
            error = "An account with this email already exists."
        else:
            uid = store.create_user(current_app, name, email,
                                    generate_password_hash(password), f"{name.split()[0]}'s workspace")
            session["uid"] = uid
            store.ensure_demo(current_app, uid)
            return redirect(url_for("dash.overview"))
        flash(error, "error")
    return render_template("register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    from flask import current_app
    if "uid" in session:
        return redirect(url_for("dash.overview"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = store.get_user_by_email(current_app, email)
        if user and check_password_hash(user["password_hash"], password):
            session["uid"] = user["id"]
            nxt = request.args.get("next") or url_for("dash.overview")
            return redirect(nxt)
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
