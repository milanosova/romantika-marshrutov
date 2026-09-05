"""Errors a service raises on purpose.

`Refused` is the one the web layer turns into a 422 with its message shown to the person, so
the message is written in Russian and says what to change. Anything else that goes wrong in a
service is a bug and stays a 500.
"""

from __future__ import annotations


class Refused(ValueError):
    """The service will not carry the request out as given; the message is for the person."""
