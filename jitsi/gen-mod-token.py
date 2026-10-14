#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyjwt"]
# ///
"""
Generate a Jitsi moderator JWT token for the FOSS United team.

Two modes:

  Named token (one person, optionally one room):
      python gen-mod-token.py <name> [room]
      python gen-mod-token.py Dilip
      python gen-mod-token.py Dilip MySpecificRoom

  Universal token (share with the whole team):
      python gen-mod-token.py --universal
      python gen-mod-token.py -u

The universal token has room "*" (valid for every room) and NO name baked in,
so each person sets their own display name in the prejoin screen and still joins
as moderator. One token, any room, each user's own name.

Security: the universal token is a shared secret. Anyone who has it is moderator
on every room until it expires. Keep it inside the team, and rotate or shorten
EXPIRY_DAYS if it may have leaked. Guests without a token still join (JWT_ALLOW_EMPTY);
the token only grants moderator.

Requires: pip install PyJWT
Reads JWT_APP_SECRET and JWT_APP_ID from the environment (same as .env).
"""

import argparse
import os
import time

import jwt

SECRET = os.environ.get("JWT_APP_SECRET", "abcdef")
APP_ID = os.environ.get("JWT_APP_ID", "fossunited")
DOMAIN = "meet.fossunited.org"
EXPIRY_DAYS = 365


def make_token(name, room):
    user = {"moderator": True}
    if name:
        user["name"] = name
    payload = {
        "context": {"user": user},
        "aud": APP_ID,
        "iss": APP_ID,
        "sub": DOMAIN,
        "room": room,
        "exp": int(time.time()) + EXPIRY_DAYS * 86400,
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


def main():
    parser = argparse.ArgumentParser(
        description="Generate a Jitsi moderator JWT for FOSS United."
    )
    parser.add_argument(
        "-u", "--universal", action="store_true",
        help="shared token: all rooms, no baked-in name, each user types their own",
    )
    parser.add_argument("name", nargs="?", help="display name (named mode)")
    parser.add_argument("room", nargs="?", default="*", help="room name, default '*' (all rooms)")
    args = parser.parse_args()

    if args.universal:
        token = make_token(name=None, room="*")
        print(f"\nUniversal moderator token (all rooms, no name, valid {EXPIRY_DAYS} days):\n")
        print(f"https://{DOMAIN}/ROOM_NAME?jwt={token}\n")
        print("Share with the team. Replace ROOM_NAME with the meeting room.")
        print("Each person sets their own display name in the prejoin screen.")
        return

    if not args.name:
        parser.error("provide a <name>, or use --universal for a shared token")

    token = make_token(name=args.name, room=args.room)
    if args.room == "*":
        print(f"\nToken for {args.name} (all rooms, valid {EXPIRY_DAYS} days):\n")
        print(f"https://{DOMAIN}/ROOM_NAME?jwt={token}\n")
        print("Replace ROOM_NAME with the actual meeting room.")
    else:
        print(f"\nhttps://{DOMAIN}/{args.room}?jwt={token}\n")


if __name__ == "__main__":
    main()
