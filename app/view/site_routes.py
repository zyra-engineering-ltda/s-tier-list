import json
import secrets
import zipfile
from datetime import datetime, timezone
from app.services.logger_service import LoggerService
from app.services.audible_auth_service import AudibleAuthService
from app.controller.image_generator_controller import ImageGeneratorController
from flask import Blueprint, render_template, request, send_file, abort, url_for, redirect
from pathlib import Path

main_route = Blueprint("home", __name__)

auth = AudibleAuthService()
controller = ImageGeneratorController(Path(main_route.root_path).parent / "cover_cache")

GENERATED_DIR = Path(main_route.root_path).parent / "generated"
GENERATED_DIR.mkdir(exist_ok=True)

print(GENERATED_DIR)


@main_route.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        client, status = auth.start_auth(username, password)

        if status == "verification_required":
            # Redirect to OTP/CVF entry page
            return redirect(url_for("home.verify", username=username))

        if client:
            profile = client.get("library")
            return render_template("success.html", profile=profile)

        return "Unexpected error."

    return render_template("login.html")

@main_route.route("/verify/<username>", methods=["GET", "POST"])
def verify(username):
    if request.method == "POST":
        code = request.form.get("code")
        code_type = request.form.get("code_type", "otp").lower()

        try:
            client = auth.complete_auth(username, code, code_type)

            library = client.get(
                "library",
                num_results=999,
                response_groups="product_desc,product_attrs,media,contributors,relationships"
            )

            profile = library.get("profile", {})

            # Build HTML string from profile
            html_output = []
            for key, value in profile.items():
                for item in value:
                    html_output.append("<div>")
                    html_output.append(f"<h1><span class='title'>{item.get('publication_name', '')}</span></h1>")
                    html_output.append(f"<span class='summary'>{item.get('merchandising_summary', '')}</span><br />")
                    html_output.append("<pre>" + json.dumps(item, indent=2, ensure_ascii=False, default=str) + "</pre>")
                    html_output.append("</div>")

            final_html = "\n".join(html_output)

            print(">>> Hitting verify route and rendering success.html with test=1")
            return render_template("success.html", test='1')

        except Exception as e:
            return f"Verification failed: {e}"

    return render_template("verify.html", username=username)

@main_route.post("/generate-rank-image")
def generate_rank_image():
    if not request.form:
        abort(400, "No form data received")

    ranks = controller.extract_ranks(request.form)
    if not ranks:
        abort(400, "No ranks selected")

    urls, titles = controller.extract_meta(request.form)
    png = controller.generate_collage(ranks, urls, titles)  # BytesIO

    # Build JSON snapshot of what the user submitted
    snapshot = {
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "ranks": ranks,
        "urls": urls,
        "titles": titles,
    }

    # Create a unique token + write a ZIP to disk
    token = secrets.token_urlsafe(16)
    zip_path = GENERATED_DIR / f"{token}.zip"

    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("litrpg_tier_list.png", png.getvalue())
        zf.writestr("submission.json", json.dumps(snapshot, indent=2))

    # Render reward page with link to download
    return render_template(
        "reward.html",
        download_url=url_for("home.download_package", token=token),
    )

@main_route.get("/download/<token>")
def download_package(token: str):
    zip_path = GENERATED_DIR / f"{token}.zip"
    if not zip_path.exists():
        abort(404)
    # Stream the ZIP; change name if you want a timestamp
    return send_file(
        str(zip_path),
        mimetype="application/zip",
        as_attachment=True,
        download_name="litrpg_rank_reward.zip",
    )