// cart_global.js: Improved - 1 + toggle, accessibility, navbar-only counter, and persistence

function updateCartUI(newTotal) {
    const navCart = document.getElementById('nav-cart-count');
    if (navCart) navCart.innerText = newTotal;
    const ariaLive = document.getElementById('aria-live-quote');
    if (ariaLive) ariaLive.innerText = `Added to Quote. Total units: ${newTotal}.`;
}

function insertUnitControls(btn, sku, tier, price, units) {
    btn.style.display = 'none';
    let container = btn.closest('.product-card') || btn.closest('.product-container') || btn.parentElement;
    let controls = document.createElement('div');
    controls.className = 'qty-controls flex items-center gap-2';
    controls.setAttribute('aria-label', 'Adjust units for this item');
    controls.innerHTML = `
        <button class='rose-btn px-3 py-1 rounded-lg minus-btn' aria-label='Decrease quantity' tabindex="0">-</button>
        <span id='units-${sku}-${tier}' tabindex="0" aria-label="Current quantity">${units}</span>
        <button class='rose-btn px-3 py-1 rounded-lg plus-btn' aria-label='Increase quantity' tabindex="0">+</button>
    `;
    container.appendChild(controls);
    controls.querySelector('.minus-btn').onclick = function() {
        if (units > 1) {
            updateUnits(sku, tier, units - 1, container, btn, price);
        } else {
            controls.querySelector('.minus-btn').disabled = true;
            updateUnits(sku, tier, 0, container, btn, price);
        }
    };
    controls.querySelector('.plus-btn').onclick = function() {
        updateUnits(sku, tier, units + 1, container, btn, price);
    };
    // Accessibility: focusable controls
    controls.querySelector('.minus-btn').setAttribute('tabindex', '0');
    controls.querySelector('.plus-btn').setAttribute('tabindex', '0');
    controls.querySelector(`#units-${sku}-${tier}`).setAttribute('tabindex', '0');
    controls.querySelector('.minus-btn').setAttribute('aria-label', 'Decrease quantity');
    controls.querySelector('.plus-btn').setAttribute('aria-label', 'Increase quantity');
    controls.querySelector(`#units-${sku}-${tier}`).setAttribute('aria-label', `Current quantity for ${sku}`);
}

function updateUnits(sku, tier, units, parent, btn, price) {
    fetch('/update-cart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            product_id: sku,
            qty: units,
            tier: tier,
            price: price,
            size: parent.querySelector('.size-select') ? parent.querySelector('.size-select').value : ''
        })
    })
    .then(res => res.json())
    .then(data => {
        if (units > 0) {
            parent.querySelector(`#units-${sku}-${tier}`).innerText = units;
        } else {
            parent.querySelector('.minus-btn').disabled = false;
            parent.querySelector('.plus-btn').disabled = false;
            parent.querySelector('.minus-btn').remove();
            parent.querySelector('.plus-btn').remove();
            parent.querySelector(`#units-${sku}-${tier}`).remove();
            btn.style.display = '';
        }
        updateCartUI(data.new_total);
    });
}

function addToQuoteDirect(sku) {
    const btn = document.getElementById('btn-' + sku);
    console.log('[BUGLOG] addToQuoteDirect called with SKU:', sku);
    if (!btn) {
        console.error('[BUGLOG] Button not found for SKU:', sku);
        return;
    }
    console.log('[BUGLOG] Button found:', btn);
    const card = btn.closest('.product-card');
    console.log('[BUGLOG] Closest product-card:', card);
    const tierSelect = card ? card.querySelector('.tier-select') : document.getElementById('tier-select-' + sku);
    if (!tierSelect) {
        console.error('[BUGLOG] tierSelect not found for SKU:', sku);
        return;
    }
    console.log('[BUGLOG] tierSelect:', tierSelect);
    const tier = tierSelect ? tierSelect.value : btn.getAttribute('data-tier') || 1;
    const price = tierSelect && tierSelect.selectedIndex >= 0 ? tierSelect.options[tierSelect.selectedIndex].getAttribute('data-price') : btn.getAttribute('data-price');
    const sizeSelect = card ? card.querySelector('.size-select') : null;
    if (!sizeSelect) {
        console.error('[BUGLOG] sizeSelect not found for SKU:', sku);
        return;
    }
    const size = sizeSelect ? sizeSelect.value : '';
    console.log('[BUGLOG] tier:', tier, 'price:', price, 'size:', size);
    const payload = {
        product_id: sku,
        qty: 1,
        tier: tier,
        price: price,
        size: size
    };
    console.log('[BUGLOG] addToQuoteDirect payload:', payload);
    fetch('/update-cart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(res => res.json())
    .then(data => {
        console.log('[BUGLOG] Response from /update-cart:', data);
        insertUnitControls(btn, sku, tier, price, 1);
        updateCartUI(data.new_total);
    })
    .catch(err => {
        console.error('[BUGLOG] Error in fetch /update-cart:', err);
    });
}

document.addEventListener('click', function(e) {
    if (e.target.classList.contains('add-to-quote-btn')) {
        const btn = e.target;
        const sku = btn.getAttribute('data-sku');
        console.log('[BUGLOG] Add to Quote clicked:', {btn, sku});
        let container = btn.closest('.product-card');
        if (!container) {
            // fallback for product page
            container = btn.closest('.product-info') || btn.closest('.product-container') || btn.parentElement;
        }
        console.log('[BUGLOG] Container found:', container);
        const tierSelect = container.querySelector('.tier-select') || document.getElementById('tier-select-' + sku);
        const tier = tierSelect ? tierSelect.value : btn.getAttribute('data-tier') || 1;
        const price = tierSelect && tierSelect.selectedIndex >= 0 ? tierSelect.options[tierSelect.selectedIndex].getAttribute('data-price') : btn.getAttribute('data-price');
        const size = container.querySelector('.size-select') ? container.querySelector('.size-select').value : '';
        console.log('[BUGLOG] tierSelect:', tierSelect, 'tier:', tier, 'price:', price, 'size:', size);
        const payload = {
            product_id: sku,
            qty: 1,
            tier: tier,
            price: price,
            size: size
        };
        console.log('[BUGLOG] Sending payload to /update-cart:', payload);
        fetch('/update-cart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(res => res.json())
        .then(data => {
            console.log('[BUGLOG] Response from /update-cart:', data);
            insertUnitControls(btn, sku, tier, price, 1);
            updateCartUI(data.new_total);
        })
        .catch(err => {
            console.error('[BUGLOG] Error in fetch /update-cart:', err);
        });
    }
});

function syncButtonStates() {
    fetch('/cart', { method: 'GET' })
        .then(res => res.json())
        .then(cart => {
            Object.keys(cart).forEach(key => {
                const item = cart[key];
                const btn = document.getElementById('btn-' + item.sku);
                if (btn) {
                    const card = btn.closest('.product-card');
                    const tier = item.tier;
                    const price = item.price;
                    const units = item.qty;
                    insertUnitControls(btn, item.sku, tier, price, units);
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
