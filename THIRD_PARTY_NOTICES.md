# Third-party notices

## MikroTik MAC-Telnet / EC-SRP5 authentication

The RouterOS MAC-Telnet packet layout and the EC-SRP5 authentication used from RouterOS
6.43 onwards are not publicly documented by MikroTik. Our implementation in
`src/mqs/transport/mactelnet/` (curve parameters, the `redp1` hash-to-point, the password
validator, the confirmation-code computation and the control-packet codes) is derived from
the public proof-of-concept:

- **MarginResearch/mikrotik_authentication** — https://github.com/MarginResearch/mikrotik_authentication
  Licensed under the **Apache License, Version 2.0**.

We reimplemented the protocol (the elliptic-curve arithmetic is written from scratch in pure
Python instead of depending on `ecdsa`), but the protocol constants and message flow follow
that reference. A full copy of the Apache License 2.0 is in `LICENSES/Apache-2.0.txt`.

The related write-up "MikroTik Authentication Revealed" (Margin Research, 2022) documents the
protocol design. The correctness of our port has **not** been verified against a real device;
see `docs/DIALECT_NOTES.md` (entries marked NUTNO OVĚŘIT).
