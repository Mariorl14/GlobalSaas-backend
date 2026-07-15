import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    # Local: enable debug unless FLASK_DEBUG=0. On Render, PORT is set → production.
    debug = os.environ.get("FLASK_DEBUG", "0" if os.environ.get("PORT") else "1").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
