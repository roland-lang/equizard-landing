from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Equizard")

@app.get("/", response_class=HTMLResponse)
def home():
    return """<!DOCTYPE html>
<html>
<head>
  <title>Equizard</title>
  <style>
    body { font-family: Arial, sans-serif; text-align: center; padding: 40px; }
    h1 { font-size: 48px; }
    p { font-size: 20px; }
  </style>
</head>
<body>
  <h1>Equizard</h1>
  <p>Wizard tools for equestrian events</p>
  <p>TriWizard • TetWizard • EventingWizard</p>
  <p>Coming soon…</p>
</body>
</html>"""
