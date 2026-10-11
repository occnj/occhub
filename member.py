"""OccHub entry point (gunicorn target is `member:app`).

The app is split by concern; importing the route modules registers their routes:
  core.py                 app/config, database, settings, uploads, security, auth
  schema.py               table definitions and migrations (init_db)
  youtube.py              Watch-page video lookups
  documents.py            sermon-note PDF/DOCX import, rich text, Next Steps helpers
  maintenance.py          expired events, unused uploads (also a CLI: python maintenance.py)
  routes_public.py        visitor-facing pages and forms
  routes_admin_*.py       admin panel
  special_events.py       Special Events settings; routes_special_events.py its pages
"""
from core import *
from schema import init_db
import youtube, documents, maintenance  # noqa: F401  (documents/maintenance register template filters)
import routes_public, routes_admin_core, routes_admin_content, routes_admin_system  # noqa: F401
import routes_special_events  # noqa: F401

@app.errorhandler(404)
def not_found(e): return redirect(url_for('hub'))
@app.errorhandler(500)
def server_error(e):
    app.logger.error(f"500 error: {e}")
    if request.path.startswith('/admin'):
        return f"""<html><body style="font-family:sans-serif;padding:40px;color:#333;">
        <h2 style="color:#c0392b;">&#9888; Server Error</h2>
        <p>Something went wrong saving that. Nothing was lost; go back and try again.</p>
        <p>The details are in <code>error.log</code> on the server.</p>
        <a href="/admin" style="color:#13677A;">&larr; Back to Admin</a>
        </body></html>""", 500
    return redirect(url_for('hub'))

init_db()
maintenance.purge_past_events()

if __name__=='__main__':
    app.run(debug=False,host='0.0.0.0',port=5500)

