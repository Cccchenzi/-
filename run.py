"""Start the local Zhiwei classroom application."""
import argparse
import threading
import webbrowser

from backend import create_app


def main():
    parser = argparse.ArgumentParser(description="Zhiwei personal knowledge assistant")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    app = create_app()
    url = f"http://127.0.0.1:{args.port}"
    print(f"\nZhiwei is starting at {url}\nPress Ctrl+C to stop.\n", flush=True)
    if args.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
