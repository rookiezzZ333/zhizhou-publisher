"""Discard a closed browser before launching again in the same request."""
def live_page(owner):
    from playwright.sync_api import Error
    try:
        if owner.page is not None and not owner.page.is_closed():
            # Flush close events before trusting cached is_closed state.
            owner.page.title()
            return owner.page
        if owner.context is not None:
            for page in owner.context.pages:
                if not page.is_closed():
                    page.title()
                    owner.page=page
                    return page
    except Error:
        pass
    stale=owner.context
    owner.context=None
    owner.page=None
    if stale is not None:
        try:stale.close()
        except Error:pass
    return None
