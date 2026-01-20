from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Equizard")

# Serve static files (logo, later CSS etc)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
def home():
    return """<!DOCTYPE html>
<html>
<head>
  <title>Equizard</title>
  <style>
    body { font-family: Arial, sans-serif; text-align: center; padding: 40px; }
    h1 { font-size: 48px; margin-bottom: 10px; }
    p { font-size: 20px; }
    img { max-width: 300px; margin-bottom: 20px; }
  </style>
</head>
<body>
  <img src="/static/equizard-logo.png" alt="Equizard logo">
  <h1>Equizard</h1>
  <p>Wizard tools for equestrian events</p>
  <p>TriWizard • TetWizard • EventingWizard</p>
  <p>Coming soon…</p>
</body>
</html>"""
