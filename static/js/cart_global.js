// cart_global.js: Improved - 1 + toggle, accessibility, navbar-only counter, and persistence

function updateCartUI(newTotal) {
    const navCart = document.getElementById('nav-cart-count');
    if (navCart) navCart.innerText = newTotal;
    const ariaLive = document.getElementById('aria-live-quote');
    if (ariaLive) ariaLive.innerText = `Added to Quote. Total units: ${newTotal}.`;
}

function insertUnitControls(btn, productId, tier, price, units, size) {
    btn.style.display = 'none';
    let container = btn.closest('.product-card') || btn.closest('.product-container') || btn.parentElement;
    let controls = document.createElement('div');
    controls.className = 'qty-controls flex items-center gap-2';
    controls.setAttribute('aria-label', 'Adjust units for this item');
    const safeSize = (size || '').replace(/[^a-zA-Z0-9_-]/g, '');
    controls.innerHTML = `
        <button class='rose-btn px-3 py-1 rounded-lg minus-btn' aria-label='Decrease quantity' tabindex="0">-</button>
        <span id='units-${productId}-${tier}-${safeSize}' tabindex="0" aria-label="Current quantity">${units}</span>
        <button class='rose-btn px-3 py-1 rounded-lg plus-btn' aria-label='Increase quantity' tabindex="0">+</button>
    `;
    container.appendChild(controls);
    controls.querySelector('.minus-btn').onclick = function() {
        let currentTier = container.querySelector('.tier-select') ? container.querySelector('.tier-select').value : tier;
        let currentSize = container.querySelector('.size-select') ? container.querySelector('.size-select').value : size;
        if (units > 1) {
            updateUnits(productId, currentTier, units - 1, container, btn, price, currentSize);
        } else {
            controls.querySelector('.minus-btn').disabled = true;
            updateUnits(productId, currentTier, 0, container, btn, price, currentSize);
        }
    };
    controls.querySelector('.plus-btn').onclick = function() {
        let currentTier = container.querySelector('.tier-select') ? container.querySelector('.tier-select').value : tier;
        let currentSize = container.querySelector('.size-select') ? container.querySelector('.size-select').value : size;
        updateUnits(productId, currentTier, units + 1, container, btn, price, currentSize);
    };
    // Accessibility: focusable controls
    controls.querySelector('.minus-btn').setAttribute('tabindex', '0');
    controls.querySelector('.plus-btn').setAttribute('tabindex', '0');
    controls.querySelector(`#units-${productId}-${tier}-${safeSize}`).setAttribute('tabindex', '0');
    controls.querySelector('.minus-btn').setAttribute('aria-label', 'Decrease quantity');
    controls.querySelector('.plus-btn').setAttribute('aria-label', 'Increase quantity');
    controls.querySelector(`#units-${productId}-${tier}-${safeSize}`).setAttribute('aria-label', `Current quantity for ${productId}`);
}

function updateUnits(productId, tier, units, parent, btn, price, size) {
    let currentTier = parent.querySelector('.tier-select') ? parent.querySelector('.tier-select').value : tier;
    let currentSize = parent.querySelector('.size-select') ? parent.querySelector('.size-select').value : size;
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
        const safeSize = (currentSize || '').replace(/[^a-zA-Z0-9_-]/g, '');
        let counter = parent.querySelector(`#units-${productId}-${currentTier}-${safeSize}`);
        // Try to get the updated qty from backend response
        let newQty = units;
        if (data.cart && data.cart_count) {
            // If backend returns cart, try to find the item
            // (not implemented in backend, but future-proof)
        }
        if (typeof data.new_qty !== 'undefined') {
            newQty = data.new_qty;
        }
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
    });
}

function addToQuoteDirect(productId) {
    const btn = document.getElementById('btn-' + productId);
    if (!btn) return;
    const card = btn.closest('.product-card');
    const tierSelect = card ? card.querySelector('.tier-select') : document.getElementById('tier-select-' + productId);
    if (!tierSelect) return;
    const tier = tierSelect ? tierSelect.value : btn.getAttribute('data-tier') || 1;
    const price = tierSelect && tierSelect.selectedIndex >= 0 ? tierSelect.options[tierSelect.selectedIndex].getAttribute('data-price') : btn.getAttribute('data-price');
    const sizeSelect = card ? card.querySelector('.size-select') : null;
    if (!sizeSelect) return;
    const size = sizeSelect ? sizeSelect.value : '';
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
            Object.keys(cart).forEach(key => {
                const item = cart[key];
                const btn = document.getElementById('btn-' + (item.product_id || item.sku || item.id));
                if (btn) {
                    const card = btn.closest('.product-card');
                    const tier = item.tier;
                    const price = item.price;
                    const units = item.qty;
                    const size = item.size || '';
                    insertUnitControls(btn, item.product_id || item.sku || item.id, tier, price, units, size);
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
