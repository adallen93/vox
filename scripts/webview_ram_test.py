"""
Minimal WebView2 RAM test.

Opens a single pywebview window and holds it open so you can measure
how much private RAM msedgewebview2.exe processes add on top of Python.

Usage:
    uv run python scripts/webview_ram_test.py

While it's running, measure in a separate PowerShell:
    Get-Process python,msedgewebview2 | Select Name, Id,
        @{n='Priv_MB';e={[math]::Round($_.PrivateMemorySize64/1MB,1)}}

Compare total Priv_MB here vs `uv run vox --ui` (Tkinter, no webview).
"""
import webview

window = webview.create_window(
    "WebView2 RAM test",
    html="<body style='background:#1a1a1a;color:#eee;font-family:monospace;padding:2rem'>"
         "<h2>WebView2 RAM test</h2><p>Measure RAM now, then close this window.</p>"
         "</body>",
)
webview.start()
