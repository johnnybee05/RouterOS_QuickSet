"""The elliptic curve MikroTik uses for EC-SRP5.

RouterOS runs SRP over Curve25519 expressed in short Weierstrass form y^2 = x^3 + a*x + b
(mod p = 2^255-19). Public values are exchanged as the Montgomery x-coordinate plus the
parity of the Weierstrass y-coordinate, exactly as the reference implementation does.

The curve constants, the Montgomery<->Weierstrass shift and the redp1 hash-to-point come
from MarginResearch/mikrotik_authentication (Apache-2.0). The point arithmetic here is a
plain from-scratch short-Weierstrass implementation over F_p (no external dependency).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

P = 2**255 - 19  # field prime
# Order of the base point (Curve25519 group order l).
ORDER = 0x1000000000000000000000000000000014DEF9DEA2F79CD65812631A5CF5D3ED
MONT_A = 486662  # Curve25519 Montgomery coefficient
# Short-Weierstrass coefficients used by RouterOS.
A = 0x2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA984914A144
B = 0x7B425ED097B425ED097B425ED097B425ED097B425ED097B4260B5E9C7710C864


def _modinv(a: int, p: int = P) -> int:
    return pow(a % p, -1, p)


def mod_sqrt(a: int, p: int = P) -> list[int]:
    """Square roots of a modulo the prime p (Tonelli-Shanks); [] if a is not a residue."""
    a %= p
    if a == 0:
        return [0]
    if p == 2:
        return [a]
    if pow(a, (p - 1) // 2, p) != 1:
        return []
    if p % 4 == 3:
        x = pow(a, (p + 1) // 4, p)
        return [x, p - x]
    q, s = p - 1, 0
    while q % 2 == 0:
        s += 1
        q //= 2
    z = 2
    while pow(z, (p - 1) // 2, p) != p - 1:
        z += 1
    c = pow(z, q, p)
    x = pow(a, (q + 1) // 2, p)
    t = pow(a, q, p)
    m = s
    while t != 1:
        i, e = 0, 2
        for i in range(1, m):  # noqa: B007 - i is used after the loop (Tonelli-Shanks)
            if pow(t, e, p) == 1:
                break
            e *= 2
        b = pow(c, 2 ** (m - i - 1), p)
        x = (x * b) % p
        t = (t * b * b) % p
        c = (b * b) % p
        m = i
    return [x, p - x]


# Montgomery x = Weierstrass x - A/3; both directions are used when converting points.
_SHIFT = MONT_A * _modinv(3) % P  # A/3


@dataclass(frozen=True)
class Point:
    """Affine short-Weierstrass point; x is None for the point at infinity."""

    x: int | None
    y: int | None = None

    @property
    def is_infinity(self) -> bool:
        return self.x is None


INFINITY = Point(None, None)


def is_on_curve(pt: Point) -> bool:
    if pt.is_infinity:
        return True
    return (pt.y * pt.y - (pt.x**3 + A * pt.x + B)) % P == 0


def negate(pt: Point) -> Point:
    if pt.is_infinity:
        return pt
    return Point(pt.x, (-pt.y) % P)


def add(p1: Point, p2: Point) -> Point:
    if p1.is_infinity:
        return p2
    if p2.is_infinity:
        return p1
    if p1.x == p2.x and (p1.y + p2.y) % P == 0:
        return INFINITY
    if p1.x == p2.x:
        slope = (3 * p1.x * p1.x + A) * _modinv(2 * p1.y) % P
    else:
        slope = (p2.y - p1.y) * _modinv(p2.x - p1.x) % P
    x = (slope * slope - p1.x - p2.x) % P
    y = (slope * (p1.x - x) - p1.y) % P
    return Point(x, y)


def scalar_mult(k: int, pt: Point) -> Point:
    """k * pt, with the scalar reduced modulo the group order (as the reference does)."""
    k %= ORDER
    result = INFINITY
    addend = pt
    while k:
        if k & 1:
            result = add(result, addend)
        addend = add(addend, addend)
        k >>= 1
    return result


def lift_x(mont_x: int, parity: int) -> Point | None:
    """Weierstrass point from a Montgomery x-coordinate and the wanted y parity.

    Returns None when no point has that x. `parity` is the low bit of the y-coordinate.
    """
    mont_x %= P
    y_squared = (mont_x**3 + MONT_A * mont_x * mont_x + mont_x) % P
    roots = mod_sqrt(y_squared)
    if not roots:
        return None
    weier_x = (mont_x + _SHIFT) % P
    for y in roots:
        if y & 1 == (parity & 1):
            return Point(weier_x, y)
    return Point(weier_x, roots[0])


def to_montgomery(pt: Point) -> tuple[bytes, int]:
    """Montgomery x-coordinate (32 bytes, big-endian) and the parity of the y-coordinate."""
    assert not pt.is_infinity, "cannot encode the point at infinity"
    mont_x = (pt.x - _SHIFT) % P
    return mont_x.to_bytes(32, "big"), pt.y & 1


GENERATOR = lift_x(9, 0)  # Curve25519 base point (u = 9), even y
assert GENERATOR is not None


def generator_multiple(scalar: int | bytes) -> tuple[bytes, int]:
    """`scalar * G`, encoded the way RouterOS transmits public values."""
    if isinstance(scalar, bytes):
        scalar = int.from_bytes(scalar, "big")
    return to_montgomery(scalar_mult(scalar, GENERATOR))


def redp1(data: bytes, parity: int) -> Point:
    """Deterministic hash-to-point: hash until an x that lifts to a valid point is found."""
    x = hashlib.sha256(data).digest()
    while True:
        candidate = hashlib.sha256(x).digest()
        point = lift_x(int.from_bytes(candidate, "big"), parity)
        if point is not None:
            return point
        x = ((int.from_bytes(x, "big") + 1) & (2**256 - 1)).to_bytes(32, "big")


def password_validator(username: str, password: str, salt: bytes) -> bytes:
    """The 32-byte SRP validator scalar i = SHA256(salt | SHA256(username:password))."""
    if len(salt) != 16:
        raise ValueError("salt must be 16 bytes")
    inner = hashlib.sha256(f"{username}:{password}".encode()).digest()
    return hashlib.sha256(salt + inner).digest()
