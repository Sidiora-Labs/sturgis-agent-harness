"""Run the model gateway independently: python -m model_gateway serve."""
import argparse
from pathlib import Path

from model_gateway.api import create_app
from model_gateway.network import NetworkPolicy


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sturgis Model Gateway — standalone model infrastructure")
    parser.add_argument("command", choices=["serve"])
    parser.add_argument("--root", type=Path, default=Path.home() / ".local/share/sturgis-gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--public-origin")
    parser.add_argument("--private-origin", action="append", default=[])
    parser.add_argument("--deny-local-servers", action="store_true")
    parser.add_argument("--web-root", type=Path)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Choose a port between 1 and 65535.")
    if args.host in {"0.0.0.0", "::"} and not args.public_origin:
        parser.error("A wildcard listener requires --public-origin.")
    host = "[" + args.host + "]" if ":" in args.host else args.host
    public = args.public_origin or f"http://{host}:{args.port}"
    web_root = args.web_root
    if web_root is None:
        built = Path(__file__).resolve().parents[1] / "consumer/web"
        if (built / "gateway.html").is_file():
            web_root = built
    app = create_app(args.root, public_origin=public, network_policy=NetworkPolicy(private_origins=args.private_origin, timeout=120),
                     allow_local_servers=not args.deny_local_servers, web_root=web_root)
    print(f"Model Gateway: {public}\nManagement key file: {app.state.auth.key_path}", flush=True)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False, access_log=False, limit_concurrency=128)


if __name__ == "__main__":
    main()
