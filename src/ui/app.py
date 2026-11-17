from __future__ import annotations

import html as html_lib

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src.config import load_config
from src.query.pipeline import ask

app = FastAPI(title="Groww MF FAQ Assistant")
cfg = load_config()


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/meta")
def meta():
    return {
        "welcome": cfg.ui["welcome"],
        "disclaimer": cfg.ui["disclaimer"],
        "examples": cfg.ui["examples"],
    }


@app.post("/api/ask")
def api_ask(req: AskRequest):
    answer = ask(req.question, cfg)
    show_trace = answer.action not in {"pii_refuse"}
    hits = []
    if show_trace:
        hits = [
            {
                "id": h.get("id"),
                "score": h.get("score"),
                "source_url": h.get("source_url"),
                "page_type": h.get("page_type"),
            }
            for h in (answer.hits or [])
        ]
    return {
        "text": answer.text,
        "source_url": answer.source_url,
        "last_updated": answer.last_updated,
        "action": answer.action,
        "hits": hits,
        "disclaimer": cfg.ui["disclaimer"],
    }


@app.get("/", response_class=HTMLResponse)
def index():
    examples = cfg.ui["examples"]
    buttons = "".join(
        f'<button type="button" class="chip" data-q="{html_lib.escape(ex, quote=True)}">'
        f"{html_lib.escape(ex)}</button>"
        for ex in examples
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>HDFC MF FAQ Assistant</title>
  <style>
    :root {{ font-family: ui-sans-serif, system-ui, sans-serif; color: #102033; background: #f4f7fb; }}
    body {{ max-width: 760px; margin: 32px auto; padding: 0 16px; }}
    h1 {{ font-size: 1.4rem; margin-bottom: 4px; }}
    .sub {{ color: #4a5b6b; margin-bottom: 12px; }}
    .disclaimer {{ background: #fff4d6; border: 1px solid #f0d58c; padding: 10px 12px; border-radius: 8px; font-weight: 600; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0; }}
    .chip {{ background: #fff; border: 1px solid #c9d6e5; border-radius: 999px; padding: 8px 12px; cursor: pointer; text-align: left; }}
    form {{ display: flex; gap: 8px; }}
    input[type=text] {{ flex: 1; padding: 10px 12px; border-radius: 8px; border: 1px solid #c9d6e5; }}
    button.send {{ background: #0b6bcb; color: #fff; border: 0; border-radius: 8px; padding: 10px 16px; cursor: pointer; }}
    .card {{ background: #fff; border: 1px solid #d9e3ee; border-radius: 12px; padding: 16px; margin-top: 16px; }}
    a {{ color: #0b6bcb; }}
    details {{ margin-top: 12px; color: #4a5b6b; }}
    .err {{ color: #9b1c1c; }}
  </style>
</head>
<body>
  <h1>HDFC Mutual Fund FAQ</h1>
  <p class="sub">{cfg.ui["welcome"]}</p>
  <div class="disclaimer">{cfg.ui["disclaimer"]}</div>
  <div class="chips">{buttons}</div>
  <form id="f">
    <input id="q" type="text" placeholder="Ask a factual question…" autocomplete="off"/>
    <button class="send" type="submit">Ask</button>
  </form>
  <div id="out"></div>
  <script>
    const q = document.getElementById('q');
    const out = document.getElementById('out');
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({{
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }}[c]));
    document.querySelectorAll('.chip').forEach(b => b.onclick = () => {{ q.value = b.dataset.q; ask(); }});
    document.getElementById('f').onsubmit = (e) => {{ e.preventDefault(); ask(); }};
    async function ask() {{
      const question = q.value.trim();
      if (!question) return;
      out.innerHTML = '<div class="card">Looking up official pages…</div>';
      const res = await fetch('/api/ask', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{question}})
      }});
      const data = await res.json();
      let html = '<div class="card">';
      html += '<p>' + esc(data.text) + '</p>';
      if (data.source_url) html += '<p><strong>Source:</strong> <a href="'+esc(data.source_url)+'" target="_blank" rel="noopener">'+esc(data.source_url)+'</a></p>';
      if (data.last_updated) html += '<p>Last updated from sources: '+esc(data.last_updated)+'</p>';
      if (data.action !== 'pii_refuse' && data.hits && data.hits.length) {{
        html += '<details><summary>RAG trace</summary><pre>' +
          esc(JSON.stringify(data.hits, null, 2)) + '</pre></details>';
      }}
      html += '</div>';
      out.innerHTML = html;
    }}
  </script>
</body>
</html>"""


def main() -> None:
    import uvicorn

    uvicorn.run("src.ui.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
