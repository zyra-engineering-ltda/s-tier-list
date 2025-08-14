
import json
from app.services.logger_service import LoggerService
from jinja2.runtime import Undefined
from datetime import datetime

def register_site_filters(app):
    @app.template_filter("from_json")
    def from_json_filter(s):
        logger = LoggerService.get_logger()
        try:
            if s is None or isinstance(s, Undefined):
                return {}
            if isinstance(s, dict):
                return s
            return json.loads(s)
        except Exception as e:
            logger.error(f"from_json error: {e}")
            return {}
        
    @app.template_filter("to_hours")
    def to_hours_filter(s):
        logger = LoggerService.get_logger()
        try:
            total_minutes = int(s)  # ensure it's an integer
            hours = total_minutes // 60
            minutes = total_minutes % 60
            if not minutes:
                return f"{hours} hours"
            else:
                return f"{hours} hours {minutes} minutes"
        except Exception as e:
            logger.error(f"to_hours error: {e}")
            return "Hours: 0 Mins: 0"

    @app.template_filter('format_iso')
    def format_iso(value, fmt="%b %d, %Y %I:%M %p"):
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value  # return as-is if parsing fails
        return value.strftime(fmt)

    @app.template_filter('pretty_json')
    def pretty_json(value):
        return json.dumps(value, indent=4, sort_keys=True)