
import markdown
from pathlib import Path
import re


# Paths (relative to repo root)
meta_path = Path("pages/META.md")
readme_path = Path("README.md")
index_path = Path("pages/index.html")
output_path = Path("pages/index.html")

# Read META.md and README.md
meta_md = meta_path.read_text() if meta_path.exists() else ""
readme_md = readme_path.read_text() if readme_path.exists() else ""

# Section titles to extract from README
sections = [
    ("intro", r"(^# .+?)(?=^##|\Z)"),
    ("project-structure", r"(^## Project structure.+?)(?=^##|\Z)"),
    ("quick-start", r"(^## Quick start.+?)(?=^##|\Z)"),
    ("core-commands", r"(^## Core commands.+?)(?=^##|\Z)"),
    ("cli-skeleton", r"(^### CLI skeleton.+?)(?=^###|^##|\Z)"),
    ("status-command", r"(^### Status command.+?)(?=^###|^##|\Z)"),
    ("importing-system-caddyfiles", r"(^### Importing system Caddyfiles.+?)(?=^###|^##|\Z)"),
    ("privileged-helper", r"(^#### Privileged helper.+?)(?=^####|^###|^##|\Z)"),
    ("admin-api-probe", r"(^#### Admin API probe.+?)(?=^####|^###|^##|\Z)"),
    ("interactive-menu", r"(^## Interactive menu overview.+?)(?=^##|\Z)"),
    ("database-schema", r"(^## Database schema builder.+?)(?=^##|\Z)"),
    ("import-export-hooks", r"(^## Import/export hooks.+?)(?=^##|\Z)"),
    ("example-workflow", r"(^## Example workflow.+?)(?=^##|\Z)"),
    ("how-to-run-locally", r"(^## How to run locally.+?)(?=^##|\Z)"),
    ("publishing-to-pypi", r"(^## Publishing to PyPI.+?)(?=^##|\Z)"),
    ("wishlist", r"(^## Wishlist.+?)(?=^##|\Z)")
]

extracted = {}
if readme_md:
    for key, pattern in sections:
        match = re.search(pattern, readme_md, re.M | re.S)
        extracted[key] = match.group(1) if match else ""


# Extract meta tags, schema.org, and Open Graph from META.md
meta_tags = []
schema_org = []
open_graph = []
for line in meta_md.splitlines():
    if line.strip().startswith('<meta '):
        meta_tags.append(line.strip())
    elif line.strip().startswith('<script '):
        schema_org.append(line.strip())
    elif line.strip().startswith('<meta property="og:'):
        open_graph.append(line.strip())

# Compose meta head HTML
meta_head_html = '\n'.join(meta_tags + schema_org + open_graph)


# Convert markdown to HTML for README sections
section_html = {}
for k, v in extracted.items():
    if k == "project-structure":
        # Find code block in markdown and wrap in div
        code_block = re.search(r'```([\s\S]+?)```', v)
        if code_block:
            html_block = f'<div class="project-structure-block">{code_block.group(1).strip()}</div>'
            # Remove code block from markdown and add after
            v_no_code = re.sub(r'```[\s\S]+?```', '', v)
            section_html[k] = markdown.markdown(v_no_code) + html_block
        else:
            section_html[k] = markdown.markdown(v)
    elif k == "core-commands":
        # Render markdown table as HTML table
        section_html[k] = markdown.markdown(v, extensions=["markdown.extensions.tables"])
    else:
        section_html[k] = markdown.markdown(v)

# Read index.html template
html = index_path.read_text() if index_path.exists() else ""

# Replace META_HEAD placeholder in <head>
html = re.sub(r'<!-- META_HEAD -->', meta_head_html, html)

# Replace README sections in body
html = re.sub(r"<section id=\"readme-intro\">.*?</section>", f"<section id=\"readme-intro\">{section_html.get('intro','')}</section>", html, flags=re.S)
html = re.sub(r"<section id=\"readme-usage\">.*?</section>", f"<section id=\"readme-usage\">{section_html.get('quick-start','')}</section>", html, flags=re.S)
html = re.sub(r"<section id=\"readme-other\">.*?</section>", f"<section id=\"readme-other\">{section_html.get('core-commands','')}{section_html.get('project-structure','')}{section_html.get('cli-skeleton','')}{section_html.get('status-command','')}{section_html.get('importing-system-caddyfiles','')}{section_html.get('privileged-helper','')}{section_html.get('admin-api-probe','')}{section_html.get('interactive-menu','')}{section_html.get('database-schema','')}{section_html.get('import-export-hooks','')}{section_html.get('example-workflow','')}{section_html.get('how-to-run-locally','')}{section_html.get('publishing-to-pypi','')}{section_html.get('wishlist','')}</section>", html, flags=re.S)

# Write output
output_path.write_text(html)
print("Built index.html from META.md and README.md sections.")
