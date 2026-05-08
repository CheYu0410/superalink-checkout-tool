#!/usr/bin/env python3
"""
Email eligibility checker for Superalink checkout tool.
Detects if an email has already used the coupon (first-purchase-only).

Usage:
    from email_checker import check_email_eligibility
    
    result = check_email_eligibility(order_id, email, session, headers)
    if not result["ok"]:
        print(result["error"])  # "该优惠券仅在首次购买时可用。"
"""

import json
import time

# In-memory cache for email eligibility results
EMAIL_ELIGIBILITY_CACHE = {}
CACHE_TTL = 1800  # 30 minutes


def normalize_email(email):
    """Normalize email for consistent cache keys."""
    return (email or "").strip().lower()


def eligibility_cache_key(order_id, email):
    """Generate a cache key for order_id + email combination."""
    return f"{order_id}:{normalize_email(email)}"


def remember_email_ineligible(order_id, email, reason="该优惠券仅在首次购买时可用。"):
    """Cache that an email is ineligible for the coupon on this order."""
    if order_id and email:
        EMAIL_ELIGIBILITY_CACHE[eligibility_cache_key(order_id, email)] = {
            "expires": time.time() + CACHE_TTL,
            "reason": reason
        }


def cached_email_ineligible(order_id, email):
    """Check if an email was previously marked ineligible. Returns reason or None."""
    _cleanup_cache()
    item = EMAIL_ELIGIBILITY_CACHE.get(eligibility_cache_key(order_id, email))
    return item.get("reason") if item else None


def _cleanup_cache():
    """Remove expired entries from the cache."""
    now = time.time()
    for k in list(EMAIL_ELIGIBILITY_CACHE.keys()):
        if EMAIL_ELIGIBILITY_CACHE[k].get("expires", 0) < now:
            EMAIL_ELIGIBILITY_CACHE.pop(k, None)


def is_coupon_error(response_data):
    """
    Analyze API response to detect coupon-related errors.
    
    Args:
        response_data: Parsed JSON response from Superalink API
        
    Returns:
        True if the response indicates a coupon error
    """
    msg_blob = json.dumps(response_data, ensure_ascii=False).lower()
    
    coupon_keywords = [
        "first", "首次", "used", "not applicable", "invalid",
        "removed", "not"
    ]
    
    # Check for first-purchase or coupon-related error messages
    if "first" in msg_blob or "首次" in msg_blob:
        return True
    if "used" in msg_blob and "coupon" in msg_blob:
        return True
    if "not applicable" in msg_blob:
        return True
    if "invalid" in msg_blob and "coupon" in msg_blob:
        return True
    
    # Check coupon-specific fields
    coupons = response_data.get("coupons") if isinstance(response_data, dict) else []
    if isinstance(coupons, list):
        for coupon in coupons:
            coupon_blob = json.dumps(coupon, ensure_ascii=False).lower()
            if "removed" in coupon_blob or "invalid" in coupon_blob or "not" in coupon_blob:
                return True
    
    return False


def friendly_email_error(msg):
    """
    Convert technical error messages to user-friendly Chinese messages.
    
    Args:
        msg: Raw error message string
        
    Returns:
        User-friendly error message in Chinese
    """
    msg = str(msg or "邮箱校验失败")
    if "首次购买" in msg or "first" in msg.lower():
        return "该优惠券仅在首次购买时可用，请更换未购买过的邮箱。"
    return msg


def check_email_eligibility(order_id, email, session, headers, update_recipient_fn):
    """
    Check if an email is eligible for the coupon on this order.
    
    Args:
        order_id: Superalink order ID
        email: Email to check
        session: requests.Session with cookies bound
        headers: HTTP headers for API calls
        update_recipient_fn: Function to call update_recipient_email
        
    Returns:
        dict: {"ok": bool, "error": str|None, "message": str|None}
    """
    email = normalize_email(email)
    
    if not email:
        return {"ok": False, "error": "请先填写接收 eSIM 的邮箱", "message": None}
    
    # Check cache first
    cached_reason = cached_email_ineligible(order_id, email)
    if cached_reason:
        return {"ok": False, "error": cached_reason, "message": None}
    
    try:
        # Try to update the email via Superalink API
        result = update_recipient_fn(session, headers, order_id, email, subscribe=False)
        
        # Check if the response indicates a coupon error
        if result is None:
            return {"ok": True, "error": None, "message": "邮箱可用，优惠券仍可用。"}
        
        if is_coupon_error(result):
            reason = "该优惠券仅在首次购买时可用。"
            remember_email_ineligible(order_id, email, reason)
            return {"ok": False, "error": reason, "message": None}
        
        return {"ok": True, "error": None, "message": "邮箱可用，优惠券仍可用。"}
        
    except Exception as err:
        reason = str(err)
        # Check if it's a coupon-related error
        if "首次购买" in reason or "first" in reason.lower():
            friendly_reason = "该优惠券仅在首次购买时可用。"
            remember_email_ineligible(order_id, email, friendly_reason)
            return {"ok": False, "error": friendly_reason, "message": None}
        
        # Re-raise non-coupon errors
        return {"ok": False, "error": friendly_email_error(reason), "message": None}


# For standalone testing
if __name__ == "__main__":
    import sys
    print("Email Checker Module - Import this module to use.")
    print("Functions available:")
    print("  - check_email_eligibility()")
    print("  - is_coupon_error()")
    print("  - friendly_email_error()")
    print("  - remember_email_ineligible()")
    print("  - cached_email_ineligible()")
