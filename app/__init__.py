from flask import Flask
from pathlib import Path
from app.view.error_handlers import register_error_handlers
from app.view.template_filters import register_site_filters
from app.view.site_routes import main_route

TEMPLATES_DIR = (Path(__file__).parent / "view" / "templates").resolve()

def create_app(config_object: str | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=TEMPLATES_DIR
    )

    GENERATED_DIR = Path(app.root_path).parent / "generated"
    GENERATED_DIR.mkdir(exist_ok=True)

    if config_object:
        app.config.from_object(config_object)

    # Register blueprints
    app.register_blueprint(main_route)
    # app.register_blueprint(api_bp, url_prefix="/api")

    # Register centralized error handlers
    register_error_handlers(app)
    register_site_filters(app)

    return app
