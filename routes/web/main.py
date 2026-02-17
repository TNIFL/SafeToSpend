# routes/web/main.py
from flask import Blueprint, render_template

web_main_bp = Blueprint("web_main", __name__)

@web_main_bp.route("/", methods=["GET"])
def landing():
    return render_template("landing.html")
