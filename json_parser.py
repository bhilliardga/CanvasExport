import json
from pathlib import Path
import re

def strip_html(html):
    # Basic HTML tag remover
    return re.sub(r"<[^>]+>", "", html or "").replace("\n", " ").strip()

def load_json_from_folder(folder="json"):
    text_blocks = []

    for path in Path(folder).glob("*.json"):
        print(f"📂 Parsing: {path.name}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            course_name = data.get("name", "Unknown Course")
            text_blocks.append(f"📘 {course_name}")

            assignments = data.get("assignments", [])
            for a in assignments:
                title = a.get("name", "Untitled Assignment")
                description = strip_html(a.get("description", ""))
                summary = f"• {title}: {description[:150]}" if description else f"• {title}"
                text_blocks.append(summary)

        except Exception as e:
            print(f"❌ Could not parse {path.name}: {e}")
            continue

    return text_blocks
