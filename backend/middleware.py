from slowapi import Limiter
from slowapi.util import get_remote_address

# rate limiter using client IP address as key
# get_remote_address extracts IP from request
limiter = Limiter(key_func=get_remote_address)