"""meridian-control: the central control plane for meridian-node GPU agents.

Implements the node control protocol (contracts/v1 in the meridian-node repo):
enrollment with a built-in Ed25519 CA, epoch-fenced sessions, leases, desired
state generations, and observations. Durable via SQLAlchemy (SQLite by default,
Postgres via a connection URL). This is a separate service from the Meridian
gateway; it does not import or modify gateway code.

See the meridian-node DESIGN.md sections 2, 8, 10, 15, and 17.
"""

__version__ = "0.1.0"
PROTOCOL_VERSION = "v1"
