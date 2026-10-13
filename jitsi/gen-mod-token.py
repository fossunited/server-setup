#!/usr/bin/env python3
"""
Generate a Jitsi moderator JWT token for FOSS United team.

Usage:
    python gen-mod-token.py <name> [room]

    room defaults to "*" (valid for all rooms)

Examples:
    python gen-mod-token.py Dilip
    python gen-mod-token.py Dilip MySpecificRoom

Requires: pip install PyJWT
"""

import jwt
import time
import sys
import os

SECRET = os.environ.get("JWT_APP_SECRET", "abcdef")
APP_ID = os.environ.get("JWT_APP_ID", "fossunited")
DOMAIN = "meet.fossunited.org"
EXPIRY_DAYS = 365

if len(sys.argv) < 2:
    print("Usage: python gen-mod-token.py <name> [room]")
    sys.exit(1)

name = sys.argv[1]
room = sys.argv[2] if len(sys.argv) > 2 else "*"

payload = {
    "context": {
        "user": {
            "name": name,
            "moderator": True,
        }
    },
    "aud": APP_ID,
    "iss": APP_ID,
    "sub": DOMAIN,
    "room": room,
    "exp": int(time.time()) + EXPIRY_DAYS * 86400,
}

token = jwt.encode(payload, SECRET, algorithm="HS256")

if room == "*":
    print(f"\nToken for {name} (all rooms, valid {EXPIRY_DAYS} days):\n")
    print(f"https://{DOMAIN}/ROOM_NAME?jwt={token}\n")
    print("Replace ROOM_NAME with actual meeting room.")
else:
    print(f"\nhttps://{DOMAIN}/{room}?jwt={token}\n")
