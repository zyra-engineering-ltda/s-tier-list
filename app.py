# ── Standard library ────────────────────────────────────────────────────────────
import hashlib
import json
import logging
import os
import re
import secrets
import textwrap
import traceback
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

# ── Third-party ────────────────────────────────────────────────────────────────
import requests
from flask import Flask, render_template, request, send_file, abort, url_for
from PIL import Image, ImageDraw, ImageFont

# ── Local modules ──────────────────────────────────────────────────────────────
from auth import start_auth, complete_auth  # remove if not used
# from jinja2.runtime import Undefined      # uncomment only if you reference it


app = Flask(__name__)

GENERATED_DIR = Path(app.root_path) / "generated"
GENERATED_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,  # Change to INFO if you want less noise
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.cache = {}

@app.errorhandler(404)
def page_not_found(e):
    # "404.html" is your custom template
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    if app.debug:
        # In debug mode → let Flask's default debugger handle it
        # Or you can return a text dump of the traceback:
        return f"<pre>{traceback.format_exc()}</pre>", 500
    else:
        # In production mode → show custom themed template
        return render_template("500.html"), 500
    
@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        client, status = start_auth(username, password)

        if status == "verification_required":
            # Redirect to OTP/CVF entry page
            return redirect(url_for("verify", username=username))

        if client:
            profile = client.get("library")
            return render_template("success.html", profile=profile)

        return "Unexpected error."

    return render_template("login.html")

@app.route("/verify/<username>", methods=["GET", "POST"])
def verify(username):
    if request.method == "POST":
        code = request.form.get("code")
        code_type = request.form.get("code_type", "otp").lower()

        try:
            client = complete_auth(username, code, code_type)

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


@app.template_filter("from_json")
def from_json_filter(s):
    logger = logging.getLogger()
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
    logger = logging.getLogger()
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


# ---------- Parse helpers ----------
def extract_ranks(form):
    """Accepts ranks[BOOK_ID] or ranks.BOOK_ID"""
    ranks = {}
    b = re.compile(r"^ranks\[(?P<id>.+?)\]$")
    d = re.compile(r"^ranks\.(?P<id>.+)$")
    for k, v in form.items():
        m = b.match(k) or d.match(k)
        if m and v:
            ranks[m.group("id")] = v
    return ranks

def extract_meta(form):
    """Pick up {{ book_id }}-url and {{ book_id }}-title"""
    urls, titles = {}, {}
    for k, v in form.items():
        if k.endswith("-url"):
            book_id = k[:-4]
            urls[book_id] = v
        elif k.endswith("-title"):
            book_id = k[:-6]
            titles[book_id] = v
    return urls, titles

# ---------- Image fetching (with simple on-disk cache) ----------
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cover_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

def _cache_path(url: str) -> str:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return os.path.join(CACHE_DIR, f"{h}.jpg")

def fetch_cover(url: str, timeout=8):
    """Return a PIL.Image (RGB). Falls back to None if fetch fails."""
    if not url:
        return None
    path = _cache_path(url)
    if os.path.exists(path):
        try:
            return Image.open(path).convert("RGB")
        except Exception:
            pass

    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
        return Image.open(BytesIO(r.content)).convert("RGB")
    except Exception:
        return None

# ---------- Collage generator ----------
def generate_collage(ranks, urls, titles):
    """
    ranks: {book_id: tier_key}
    urls:  {book_id: cover_url}
    titles:{book_id: title}
    """
    # You can change labels/colors/order here to match your screenshot
    TIERS = [
        ("S",   "Glorious Pantheon of Literary Deities", (234, 149, 148)),  # S Tier
        ("A",   "Champions of the Grand Narrative",      (68, 68, 68)),     # A Tier
        ("B",   "Worthy Contenders for the Hero’s Feast",(232, 213, 141)),  # B Tier
        ("C",   "Respectable Denizens of the Mid-Levels",(243, 236, 168)),  # C Tier
        ("D",   "Shaky Survivors of Chapter 3",          (214, 236, 163)),  # D Tier
        ("F",   "Fodder for the Slush Pile Golems",      (214, 236, 163)),  # F Tier
        ("DNF", "Vanquished by the Reader’s Apathy",     (230, 230, 230)),  # Not For Me / DNF
        ("ITP", "Cast Screaming Into the Pit",           (210, 210, 210)),  # Into the Pit
    ]


    # Bucket book_ids by tier, in the order above
    buckets = {key: [] for key, *_ in TIERS}
    for book_id, tier in ranks.items():
        tier = tier.upper()
        if tier not in buckets:
            tier = "F"  # unknown -> fallback
        buckets[tier].append(book_id)

    # Canvas/layout
    CANVAS_W = 1920
    MARGIN   = 24
    ROW_GAP  = 16
    LABEL_W  = 220        # left label column width
    TILE_H   = 180        # thumbnails height
    TILE_GAP = 10

    # Fonts
    try:
        font_label = ImageFont.truetype("arial.ttf", 36)
        font_small = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font_label = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Compute total height dynamically
    def row_height(n_tiles):
        if n_tiles == 0:
            return TILE_H  # empty row placeholder
        # how many tiles fit per row?
        usable_w = CANVAS_W - 2*MARGIN - LABEL_W
        # approximate tile widths after aspect (covers ~1:1 to 2:3; assume avg 0.66 ratio)
        # We'll scale each cover to TILE_H tall, keep aspect
        # Place as many as fit, wrap as needed
        # Approx width per tile after scaling; use ~TILE_H * 0.66
        approx_tile_w = int(TILE_H * 0.66) + TILE_GAP
        per_row = max(1, usable_w // approx_tile_w)
        rows = (n_tiles + per_row - 1) // per_row
        return rows * (TILE_H + TILE_GAP) - TILE_GAP

    total_h = 2*MARGIN + sum(row_height(len(buckets[t[0]])) + ROW_GAP for t in TIERS) - ROW_GAP
    img = Image.new("RGB", (CANVAS_W, total_h), (20, 20, 20))
    draw = ImageDraw.Draw(img)

    y = MARGIN
    for tier_key, label, color in TIERS:
        # label background
        draw.rectangle([MARGIN, y, MARGIN + LABEL_W, y + TILE_H], fill=color)
        # label text (centered vertically)
        tx = MARGIN + 12
        ty = y + (TILE_H - 24) // 2
        draw.text((tx, ty), label, fill=(0, 0, 0), font=font_label)

        # draw tiles
        x0 = MARGIN + LABEL_W + TILE_GAP
        x, row_y = x0, y
        usable_w = CANVAS_W - MARGIN - x0
        per_row = max(1, usable_w // (int(TILE_H * 0.66) + TILE_GAP))

        count = 0
        for book_id in buckets[tier_key]:
            cover = fetch_cover(urls.get(book_id))
            if cover is None:
                # fallback: colored block with wrapped title
                block = Image.new("RGB", (int(TILE_H * 0.66), TILE_H), (40, 40, 40))
                d2 = ImageDraw.Draw(block)
                title = titles.get(book_id, book_id)
                lines = textwrap.wrap(title, width=16)
                ty2 = 8
                for ln in lines[:6]:
                    d2.text((6, ty2), ln, fill=(230, 230, 230), font=font_small)
                    ty2 += 20
                tile = block
            else:
                # scale to height TILE_H
                w, h = cover.size
                scale = TILE_H / float(h)
                tile = cover.resize((int(w*scale), TILE_H))

            # wrap row if needed
            if count and (x + tile.size[0] > CANVAS_W - MARGIN):
                x = x0
                row_y += TILE_H + TILE_GAP

            img.paste(tile, (x, row_y))
            x += tile.size[0] + TILE_GAP
            count += 1

        # advance y by the computed row height for this tier
        y += row_height(len(buckets[tier_key])) + ROW_GAP

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ---------- Route ----------

@app.post("/generate-rank-image")
def generate_rank_image():
    if not request.form:
        abort(400, "No form data received")

    ranks = extract_ranks(request.form)
    if not ranks:
        abort(400, "No ranks selected")

    urls, titles = extract_meta(request.form)
    png = generate_collage(ranks, urls, titles)  # BytesIO

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
        download_url=url_for("download_package", token=token),
    )

@app.get("/download/<token>")
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

if __name__ == "__main__":
    app.run(debug=True)
