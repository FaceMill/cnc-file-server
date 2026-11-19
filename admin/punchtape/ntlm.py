"""NT password hash (MD4 of UTF-16LE) for Samba's passdb.

Pure Python because OpenSSL 3 no longer ships MD4 by default.
"""
import struct

_MASK = 0xFFFFFFFF


def _lrot(x, n):
    x &= _MASK
    return ((x << n) | (x >> (32 - n))) & _MASK


def _f(x, y, z):
    return (x & y) | (~x & z)


def _g(x, y, z):
    return (x & y) | (x & z) | (y & z)


def _h(x, y, z):
    return x ^ y ^ z


_ROUNDS = (
    (_f, range(16), (3, 7, 11, 19), 0),
    (_g, (0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15), (3, 5, 9, 13), 0x5A827999),
    (_h, (0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15), (3, 9, 11, 15), 0x6ED9EBA1),
)


def md4(data: bytes) -> bytes:
    msg = bytearray(data) + b"\x80"
    msg += b"\x00" * ((56 - len(msg)) % 64)
    msg += struct.pack("<Q", (8 * len(data)) & 0xFFFFFFFFFFFFFFFF)

    state = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]
    for off in range(0, len(msg), 64):
        x = struct.unpack("<16I", msg[off:off + 64])
        a, b, c, d = state
        for func, order, shifts, const in _ROUNDS:
            for i, k in enumerate(order):
                a = _lrot(a + func(b, c, d) + x[k] + const, shifts[i % 4])
                a, b, c, d = d, a, b, c
        state = [(s + v) & _MASK for s, v in zip(state, (a, b, c, d))]
    return struct.pack("<4I", *state)


def nt_hash(password: str) -> str:
    return md4(password.encode("utf-16-le")).hex().upper()
