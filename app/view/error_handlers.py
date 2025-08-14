import traceback
from flask import render_template, request, jsonify
from werkzeug.exceptions import HTTPException

def register_error_handlers(app):

    @app.errorhandler(404)
    def not_found(e):
        if wants_json():
            return jsonify(error="Not Found", status=404), 404
        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def forbidden(e):
        if wants_json():
            return jsonify(error="Forbidden", status=403), 403
        return render_template("errors/403.html"), 403

    @app.errorhandler(500)
    def server_error(e):
        if app.debug:
            # Let Flask show the debugger; or return explicit traceback:
            return f"<pre>{traceback.format_exc()}</pre>", 500
        if wants_json():
            return jsonify(error="Internal Server Error", status=500), 500
        return render_template("errors/500.html"), 500

    # Optional: catch-all for any HTTPException to keep JSON vs HTML consistent
    @app.errorhandler(HTTPException)
    def handle_http_exception(e: HTTPException):
        # This runs only if you didn’t define a more specific handler above
        if wants_json():
            return jsonify(error=e.name, status=e.code, description=e.description), e.code
        # Fall back to generic template or map by status code if you prefer
        template = {403: "errors/403.html", 404: "errors/404.html"}.get(e.code, "errors/500.html")
        return render_template(template), e.code

def wants_json() -> bool:
    # Simple heuristic: treat /api/* as JSON, or honor Accept header
    return request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", "")
