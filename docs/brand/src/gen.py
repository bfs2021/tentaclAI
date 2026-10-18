"""Brand images of tentaclAI as HTML pages – rendered to PNG by render.mjs (see scripts/build-brand.sh).

Edit the texts and styles here, then run scripts/build-brand.sh. Output: docs/brand/*.png
"""
import os
import sys

SLOGAN = "Long arms, one brain –<br>safely caged."
SUBTITLE = "Claude Code with controlled access to your Docker hosts, APIs, databases, repos and files."

W = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))  # work dir (logo.svg, topology.png)
FONT = '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">'
BASE = """*{box-sizing:border-box;margin:0;padding:0} body{font-family:Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.ai{background:linear-gradient(100deg,#a78bfa 0%,#7c9cf5 45%,#22d3ee 100%);-webkit-background-clip:text;background-clip:text;color:transparent;margin-left:.04em}"""


def page(name, w, h, css, body):
    with open(os.path.join(W, name), "w") as f:
        f.write(f'<!doctype html><html><head><meta charset="utf-8">{FONT}<style>{BASE} html,body{{width:{w}px;height:{h}px;overflow:hidden}} {css}</style></head><body>{body}</body></html>')


logo = '<img src="logo.svg" class="logo">'
WM = 'tentacl<span class="ai">AI</span>'

# GitHub social preview (1280×640): wordmark, slogan, subtitle and a tilted topology screenshot
page("social.html", 1280, 640, """body{background:linear-gradient(135deg,#120f24 0%,#0d1117 45%,#0a1a22 100%);position:relative}
.wrap{position:absolute;left:80px;top:0;bottom:0;width:580px;display:flex;flex-direction:column;justify-content:center}
.brand{display:flex;align-items:center;gap:20px}.logo{width:84px;height:84px;border-radius:20px;box-shadow:0 10px 36px rgba(124,92,240,.45)}
h1{font-size:86px;letter-spacing:-.035em;line-height:1;color:#f1f4f9;font-weight:800}
.slogan{font-size:40px;font-weight:700;color:#eef2f8;margin-top:42px;line-height:1.15}.sub{font-size:24px;color:#93a0b6;margin-top:16px;line-height:1.4}
.shot{position:absolute;left:700px;top:70px;width:900px;height:560px;border-radius:18px;overflow:hidden;border:1px solid #2a3344;box-shadow:0 30px 90px rgba(0,0,0,.6),0 0 0 1px rgba(167,139,250,.15);transform:perspective(1600px) rotateY(-14deg) rotateX(4deg);transform-origin:left center}
.shot img{width:1440px;margin-left:-370px;margin-top:-170px}""",
     f'<div class="wrap"><div class="brand">{logo}<h1>{WM}</h1></div><div class="slogan">{SLOGAN}</div>'
     f'<div class="sub">{SUBTITLE}</div></div><div class="shot"><img src="topology.png"></div>')

# wordmark (logo + name) – rendered for dark and light backgrounds, transparent
page("word.html", 1000, 220, ".w{display:flex;align-items:center;gap:26px;padding:30px 40px}.logo{width:130px;height:130px;border-radius:30px}h1{font-size:118px;letter-spacing:-.035em;font-weight:800;color:var(--t)}",
     f'<div class="w">{logo}<h1>{WM}</h1></div>')
