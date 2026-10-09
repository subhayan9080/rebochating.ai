"""Run the Relay platform (dev server)."""
import os

from relay import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"Relay running at http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=True)
