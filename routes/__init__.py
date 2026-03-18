# routes/__init__.py
from routes.web.main import web_main_bp
from routes.web.dashboard import web_dashboard_bp
from routes.web.overview import web_overview_bp
from routes.web.inbox import web_inbox_bp
from routes.web.auth import web_auth_bp  # 네 프로젝트에 이미 있다고 가정
from routes.web.bank import web_bank_bp
from routes.web.web_calendar import web_calendar_bp
from routes.web.billing import web_billing_bp
from routes.web.profile import web_profile_bp
from routes.web.support import web_support_bp
from routes.web.admin import web_admin_bp
from routes.web.nhis import web_nhis_bp
from routes.web.official_data import web_official_data_bp
from routes.web.reference_material import web_reference_material_bp

# ✅ 증빙 자료 보관함 / 세무사 전달 패키지
from routes.web.vault import web_vault_bp
from routes.web.package import web_package_bp


def register_blueprints(app):
    app.register_blueprint(web_main_bp)

    # ✅ /dashboard/: 대시보드(입력/계산)
    # 템플릿(landing/hero 등)에서 web_dashboard.* endpoint를 참조하므로 반드시 등록.
    # web_calendar_bp도 url_prefix="/dashboard"를 쓰지만 URL이 겹치지 않으면 공존 가능.
    app.register_blueprint(web_dashboard_bp, url_prefix="/dashboard")

    app.register_blueprint(web_overview_bp)
    app.register_blueprint(web_inbox_bp)
    app.register_blueprint(web_auth_bp)
    app.register_blueprint(web_bank_bp)
    app.register_blueprint(web_billing_bp)
    app.register_blueprint(web_profile_bp)
    app.register_blueprint(web_support_bp)
    app.register_blueprint(web_admin_bp)
    app.register_blueprint(web_nhis_bp)
    app.register_blueprint(web_calendar_bp)
    app.register_blueprint(web_official_data_bp)
    app.register_blueprint(web_reference_material_bp)

    # ✅ new
    app.register_blueprint(web_vault_bp)
    app.register_blueprint(web_package_bp)
