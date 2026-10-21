"""Mint a long-lived service token (e.g. for Cognify's backend) signed with POPS_JWT_SECRET.

    python scripts/issue_service_token.py <org_id> [role] [days]

Prefer OIDC client-credentials tokens from Cognify identity where available.
"""

import sys
import time
import uuid

import jwt

from app.core.config import get_settings

org, role = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "submitter")
days = int(sys.argv[3]) if len(sys.argv) > 3 else 90
now = int(time.time())
payload = {
    "sub": str(uuid.uuid5(uuid.NAMESPACE_URL, f"service:{org}:{role}")),
    "org": org,
    "role": role,
    "email": f"service-{role}@pops",
    "iat": now,
    "exp": now + days * 86400,
}
print(jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256"))
