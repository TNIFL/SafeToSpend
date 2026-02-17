# app.py
import os, click, sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from flask_migrate import Migrate

from core.extensions import db
from services.evidence_store import purge_expired_evidence

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
migrate = Migrate()


def create_app():
    app = Flask(
        __name__,
        static_folder=str(BASE_DIR / "static"),
        template_folder=str(BASE_DIR / "templates"),
    )

    # ✅ env 우선순위: SQLALCHEMY_DATABASE_URI -> DATABASE_URL
    db_uri = os.getenv("SQLALCHEMY_DATABASE_URI") or os.getenv("DATABASE_URL")
    if not db_uri:
        raise RuntimeError("SQLALCHEMY_DATABASE_URI 또는 DATABASE_URL 환경변수가 없습니다.")

    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

    # 로컬에서 쿠키가 거부되어 로그인 안되는 상황 방지용 기본값
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
    
    app.config["EVIDENCE_UPLOAD_DIR"] = os.getenv("EVIDENCE_UPLOAD_DIR") or str(BASE_DIR / "uploads" / "evidence")
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_BYTES") or (20 * 1024 * 1024))
    
    @app.cli.command("purge-evidence")
    def purge_evidence_cmd():
        n = purge_expired_evidence()
        click.echo(f"purged: {n}")


    db.init_app(app)
    migrate.init_app(app, db)

    # ✅ 모델 로딩(마이그레이션/ORM용)
    import domain.models  # noqa: F401

    # ✅ 핵심: flask db 명령은 "웹 라우트"가 필요 없는데,
    # 라우트 import가 깨져 있으면 db 명령까지 같이 죽어버림.
    # 그래서 db 관련 명령일 땐 블루프린트를 등록하지 않음.
    if "db" not in sys.argv:
        from routes import register_blueprints
        register_blueprints(app)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
