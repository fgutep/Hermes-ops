"""``hermes-ops-auth`` — key generation, one-time login, token status.

    hermes-ops-auth keygen     # prints a fresh OPS_ENCRYPTION_KEY
    hermes-ops-auth login      # opens a browser for Google consent
    hermes-ops-auth status     # prints token validity / expiry / scopes
"""

from __future__ import annotations

import argparse
import sys

from hermes_ops import config
from hermes_ops.auth import crypto, oauth


def cmd_keygen(_args: argparse.Namespace) -> int:
    print(crypto.generate_key())
    print(
        "# set this in the server environment, e.g.\n"
        "#   export OPS_ENCRYPTION_KEY=<value above>",
        file=sys.stderr,
    )
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    try:
        creds = oauth.run_login(open_browser=not args.no_browser)
    except oauth.AuthError as exc:
        print(f"login failed: {exc}", file=sys.stderr)
        return 1
    print(f"OK  token written to {config.token_path()}")
    print(f"    account : {getattr(creds, 'account', None) or '(unknown)'}")
    print(f"    scopes  : {' '.join(creds.scopes or [])}")
    print(f"    expiry  : {creds.expiry.isoformat() if creds.expiry else '(none)'}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    try:
        creds = oauth.load_credentials()
    except Exception as exc:  # noqa: BLE001 - report anything cleanly
        print(f"NOT AUTHENTICATED: {exc}", file=sys.stderr)
        return 1
    print(f"valid  : {creds.valid}")
    print(f"expiry : {creds.expiry.isoformat() if creds.expiry else '(none)'}")
    print(f"scopes : {' '.join(creds.scopes or [])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    config.load_env_file()

    parser = argparse.ArgumentParser(prog="hermes-ops-auth", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("keygen", help="print a fresh 32-byte hex key").set_defaults(
        func=cmd_keygen
    )
    login = sub.add_parser("login", help="run the interactive OAuth consent flow")
    login.add_argument(
        "--no-browser",
        action="store_true",
        help="print the URL instead of opening a browser",
    )
    login.set_defaults(func=cmd_login)
    sub.add_parser("status", help="print current token state").set_defaults(
        func=cmd_status
    )

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
