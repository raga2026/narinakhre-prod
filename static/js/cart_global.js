// cart_global.js: Improved - 1 + toggle, accessibility, navbar-only counter, and persistence

function updateCartUI(newTotal) {
    const navCart = document.getElementById('nav-cart-count');
    if (navCart) navCart.innerText = newTotal;
    const ariaLive = document.getElementById('aria-live-quote');
    if (ariaLive) ariaLive.innerText = `Added to Quote. Total units: ${newTotal}.`;
}

function insertUnitControls(btn, productId, tier, price, units, size) {
    btn.style.display = 'none';
    let container = btn.closest('.product-card') || btn.closest('.product-container');
    // Fallback: try parentElement if not found
    if (!container && btn.parentElement) {
        container = btn.parentElement;
    }
    if (!container) {
        alert('Could not find the product container for this button. Please ensure the button is inside a .product-card or .product-container.');
        console.warn('[Add to Quote] insertUnitControls: container is null! Button must be inside .product-card or .product-container.', { productId, tier, price, units, size, btn });
        return;
    }
    let controls = document.createElement('div');
    controls.className = 'qty-controls flex items-center gap-2';
    controls.setAttribute('aria-label', 'Adjust units for this item');
    // Default to first size if not provided
    let defaultSize = size;
    if (!defaultSize) {
        const sizeSelect = container.querySelector('.size-select');
        if (sizeSelect && sizeSelect.options.length > 0) {
            defaultSize = sizeSelect.value || sizeSelect.options[0].value;
        }
    }
    // Sanitize for valid CSS ID: replace . with _ and prefix with 'u'
    const safeProductId = 'u' + String(productId).replace(/[^a-zA-Z0-9_-]/g, '');
    const safeTier = String(tier).replace(/\./g, '_').replace(/[^a-zA-Z0-9_-]/g, '');
    const safeSize = (defaultSize || '').replace(/\./g, '_').replace(/[^a-zA-Z0-9_-]/g, '');
    controls.innerHTML = `
        <button class='rose-btn px-3 py-1 rounded-lg minus-btn' aria-label='Decrease quantity' tabindex="0">-</button>
        <span id='units-${safeProductId}-${safeTier}-${safeSize}' tabindex="0" aria-label="Current quantity">${units}</span>
        <button class='rose-btn px-3 py-1 rounded-lg plus-btn' aria-label='Increase quantity' tabindex="0">+</button>
    `;
    container.appendChild(controls);
    controls.querySelector('.minus-btn').onclick = function() {
        let currentTier = container.querySelector('.tier-select') ? container.querySelector('.tier-select').value : tier;
        let currentSize = container.querySelector('.size-select') ? container.querySelector('.size-select').value : size;
        // Sanitize for valid CSS ID
        const safeProductId = 'u' + String(productId).replace(/[^a-zA-Z0-9_-]/g, '');
        const safeTier = String(currentTier).replace(/\./g, '_').replace(/[^a-zA-Z0-9_-]/g, '');
        const safeSize = (currentSize || '').replace(/\./g, '_').replace(/[^a-zA-Z0-9_-]/g, '');
        let counter = container.querySelector(`#units-${safeProductId}-${safeTier}-${safeSize}`);
        let currentUnits = counter ? parseInt(counter.innerText, 10) : units;
        if (currentUnits > 1) {
            updateUnits(productId, currentTier, currentUnits - 1, container, btn, price, currentSize);
        } else {
            controls.querySelector('.minus-btn').disabled = true;
            updateUnits(productId, currentTier, 0, container, btn, price, currentSize);
        }
    };
    controls.querySelector('.plus-btn').onclick = function() {
        let currentTier = container.querySelector('.tier-select') ? container.querySelector('.tier-select').value : tier;
        let currentSize = container.querySelector('.size-select') ? container.querySelector('.size-select').value : size;
        // Sanitize for valid CSS ID
        const safeProductId = 'u' + String(productId).replace(/[^a-zA-Z0-9_-]/g, '');
        const safeTier = String(currentTier).replace(/\./g, '_').replace(/[^a-zA-Z0-9_-]/g, '');
        const safeSize = (currentSize || '').replace(/\./g, '_').replace(/[^a-zA-Z0-9_-]/g, '');
        let counter = container.querySelector(`#units-${safeProductId}-${safeTier}-${safeSize}`);
        let currentUnits = counter ? parseInt(counter.innerText, 10) : units;
        console.log('[DEBUG] Plus button clicked:', { productId, currentTier, currentSize, currentUnits });
        updateUnits(productId, currentTier, currentUnits + 1, container, btn, price, currentSize);
    };
    // Accessibility: focusable controls
    controls.querySelector('.minus-btn').setAttribute('tabindex', '0');
    controls.querySelector('.plus-btn').setAttribute('tabindex', '0');
    controls.querySelector(`#units-${safeProductId}-${safeTier}-${safeSize}`).setAttribute('tabindex', '0');
    controls.querySelector('.minus-btn').setAttribute('aria-label', 'Decrease quantity');
    controls.querySelector('.plus-btn').setAttribute('aria-label', 'Increase quantity');
    controls.querySelector(`#units-${safeProductId}-${safeTier}-${safeSize}`).setAttribute('aria-label', `Current quantity for ${productId}`);
}

function updateUnits(productId, tier, units, parent, btn, price, size) {
    let currentTier = parent.querySelector('.tier-select') ? parent.querySelector('.tier-select').value : tier;
    let currentSize = parent.querySelector('.size-select') ? parent.querySelector('.size-select').value : size;
    // Default to first size if not selected
    if ((!currentSize || currentSize === '') && parent.querySelector('.size-select')) {
        const sizeSelect = parent.querySelector('.size-select');
        if (sizeSelect.options.length > 0) {
            currentSize = sizeSelect.options[0].value;
        }
    }
    if (!productId || productId === 'null' || productId === 'undefined') {
        console.warn('[Add to Quote] JS: productId is missing in updateUnits!', { productId, tier, units, price, size });
        return;
    }
    fetch('/update-cart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            product_id: productId,
            qty: units,
            tier: currentTier,
            price: price,
            size: currentSize
        })
    })
    .then(res => res.json())
    .then(data => {
        // Sanitize for valid CSS ID
        const safeProductId = 'u' + String(productId).replace(/[^a-zA-Z0-9_-]/g, '');
        const safeTier = String(currentTier).replace(/\./g, '_').replace(/[^a-zA-Z0-9_-]/g, '');
        const safeSize = (currentSize || '').replace(/\./g, '_').replace(/[^a-zA-Z0-9_-]/g, '');
        let counter = parent.querySelector(`#units-${safeProductId}-${safeTier}-${safeSize}`);
        // Always update the UI counter to match backend new_qty
        let newQty = typeof data.new_qty !== 'undefined' ? data.new_qty : units;
        if (data.status === 'success' && counter && newQty > 0) {
            counter.innerText = newQty;
        } else if (counter) {
            parent.querySelector('.minus-btn').disabled = false;
            parent.querySelector('.plus-btn').disabled = false;
            if (parent.querySelector('.minus-btn')) parent.querySelector('.minus-btn').remove();
            if (parent.querySelector('.plus-btn')) parent.querySelector('.plus-btn').remove();
            counter.remove();
            btn.style.display = '';
        }
        updateCartUI(data.new_total);
        // Notify other tabs/pages (including checkout) to refresh
        try { window.localStorage.setItem('cart_updated', Date.now().toString()); } catch (e) {}
        // Force reload if on checkout page to show latest units
        if (window.location.pathname.includes('checkout')) {
            window.location.reload();
        }
    });
}

function addToQuoteDirect(productId) {
    if (!productId || productId === 'null' || productId === 'undefined') {
        console.warn('[Add to Quote] JS: productId is missing in addToQuoteDirect!', { productId });
        return;
    }
    // Always get the button by data-product-id, not just by id
    let btn = document.querySelector('.add-to-quote-btn[data-product-id="' + productId + '"]');
    if (!btn) btn = document.getElementById('btn-' + productId);
    if (!btn) return;
    const card = btn.closest('.product-card');
    const tierSelect = card ? card.querySelector('.tier-select') : document.getElementById('tier-select-' + productId);
    if (!tierSelect) return;
    const tier = tierSelect ? tierSelect.value : btn.getAttribute('data-tier') || 1;
    const price = tierSelect && tierSelect.selectedIndex >= 0 ? tierSelect.options[tierSelect.selectedIndex].getAttribute('data-price') : btn.getAttribute('data-price');
    const sizeSelect = card ? card.querySelector('.size-select') : null;
    let size = sizeSelect ? sizeSelect.value : '';
    // Default to first size if not selected
    if ((!size || size === '') && sizeSelect && sizeSelect.options.length > 0) {
        size = sizeSelect.options[0].value;
    }
    // Defensive: if still missing productId or size, abort
    if (!productId || !size) {
        alert('Missing product or size. Please select a size.');
        return;
    }
    const payload = {
        product_id: productId,
        qty: 1,
        tier: tier,
        price: price,
        size: size
    };
    fetch('/update-cart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(res => res.json())
    .then(data => {
        insertUnitControls(btn, productId, tier, price, 1, size);
        updateCartUI(data.new_total);
    })
    .catch(err => {
        console.error('[BUGLOG] Error in fetch /update-cart:', err);
    });
}

document.addEventListener('click', function(e) {
    if (e.target.classList.contains('add-to-quote-btn')) {
        const btn = e.target;
        const productId = btn.getAttribute('data-product-id');
        addToQuoteDirect(productId);
    }
});

function syncButtonStates() {
    fetch('/cart', { method: 'GET' })
        .then(res => res.json())
        .then(cart => {
            // For each product card, check if the current tier/size matches a cart item
            document.querySelectorAll('.product-card').forEach(card => {
                const btn = card.querySelector('.add-to-quote-btn');
                if (!btn) return;
                const productId = btn.getAttribute('data-product-id');
                const tierSelect = card.querySelector('.tier-select');
                const sizeSelect = card.querySelector('.size-select');
                const tier = tierSelect ? tierSelect.value : btn.getAttribute('data-tier') || 1;
                const size = sizeSelect ? sizeSelect.value : btn.getAttribute('data-size') || '';
                // Find cart item for this product, tier, and size
                let found = false;
                Object.keys(cart).forEach(key => {
                    const item = cart[key];
                    if ((item.product_id == productId || item.sku == productId || item.id == productId)
                        && String(item.tier) == String(tier)
                        && String(item.size) == String(size)) {
                        insertUnitControls(btn, productId, tier, item.price, item.qty, size);
                        found = true;
                    }
                });
                if (!found) {
                    // If not in cart, show Add to Quote button
                    btn.style.display = '';
                    // Remove any lingering qty-controls
                    let controls = card.querySelector('.qty-controls');
                    if (controls) controls.remove();
                }
            });
        })
        .catch(err => {
            console.error('[BUGLOG] Error syncing button states:', err);
        });
}

document.addEventListener('DOMContentLoaded', function() {
    syncButtonStates();
});

window.onload = syncButtonStates;

// --- Auto-update counter and button on tier/size change ---
document.addEventListener('change', function(e) {
    // Only handle tier or size dropdowns inside a product card
    if (e.target.classList.contains('tier-select') || e.target.classList.contains('size-select')) {
        const select = e.target;
        const card = select.closest('.product-card');
        if (!card) return;
        const btn = card.querySelector('.add-to-quote-btn');
        if (!btn) return;
        const productId = btn.getAttribute('data-product-id');
        const tierSelect = card.querySelector('.tier-select');
        const sizeSelect = card.querySelector('.size-select');
        const tier = tierSelect ? tierSelect.value : btn.getAttribute('data-tier') || 1;
        const price = tierSelect && tierSelect.selectedIndex >= 0 ? tierSelect.options[tierSelect.selectedIndex].getAttribute('data-price') : btn.getAttribute('data-price');
        const size = sizeSelect ? sizeSelect.value : btn.getAttribute('data-size') || '';
        // Always reset to 1 unit when tier/size changes
        fetch('/update-cart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                product_id: productId,
                qty: 1,
                tier: tier,
                price: price,
                size: size
            })
        })
        .then(res => res.json())
        .then(data => {
            // After updating cart, re-sync all button states so only the right counter is shown
            syncButtonStates();
            updateCartUI(data.new_total);
        });
    }
});
