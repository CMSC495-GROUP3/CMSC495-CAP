"""Shared slowapi rate limiter instance.

Imported by both main.py (to register the exception handler) and any route
that needs a rate limit decorator.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
