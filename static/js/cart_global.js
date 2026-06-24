/**
 * cart_global.js: Unified Retail & Wholesale Logic
 * Handles: Cart increments, dynamic quantity, size selection, and Navbar syncing.
 */

// 1. Helper to detect current site mode
function getSiteMode() {
    return window.location.pathname.includes('/retail') || window.location.host.includes('retail') ? 'retail' : 'wholesale';
}

// 2. Update the Navbar Cart Counter
function updateCartUI(newTotal) {
    const navCart = document.getElementById('nav-cart-count');
    if (navCart) navCart.innerText = newTotal;
    
    const ariaLive = document.getElementById('aria-live-quote');
    const msg = getSiteMode() === 'retail' ? 'Added to Cart.' : 'Added to Quote.';
    if (ariaLive) ariaLive.innerText = `${msg} Total items: ${newTotal}.`;
}

// 3. Main Add to Cart / Quote Logic
function addToCart(btn) {
    const mode = getSiteMode();
    const productId = btn.getAttribute('data-sku') || btn.getAttribute('data-product-id');
    const name = btn.getAttribute('data-name');
    let price = btn.getAttribute('data-price');
    let size = 'Standard';
    let qty = 1;
    let tier = 1;

    // Use data-tier-id and data-size-id if present
    const tierId = btn.getAttribute('data-tier-id');
    const sizeId = btn.getAttribute('data-size-id');
    if (tierId) {
        const tierEl = document.getElementById(tierId);
        if (tierEl) {
            price = tierEl.options[tierEl.selectedIndex].getAttribute('data-price');
            tier = tierEl.value;
        }
    }
    if (sizeId) {
        const sizeEl = document.getElementById(sizeId);
        if (sizeEl) {
            size = sizeEl.value;
        }
    }

    // Get Quantity (Check for PDP input first, then button attribute, else 1)
    const pdpQtyInput = document.getElementById('pdp-qty');
    if (pdpQtyInput) {
        qty = parseInt(pdpQtyInput.value);
    } else if (btn.getAttribute('data-qty')) {
        qty = parseInt(btn.getAttribute('data-qty'));
    }

    if (!productId || !size) {
        alert('Please select a size.');
        return;
    }

    const payload = {
        product_id: productId,
        qty: qty,
        tier: tier,
        price: parseFloat(price),
        size: size,
        name: name
    };

    fetch('/update-cart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            updateCartUI(data.new_total);
            insertUnitControls(btn, productId, tier, price, qty, size);
            try { window.localStorage.setItem('cart_updated', Date.now().toString()); } catch (e) {}
        }
    })
    .catch(err => console.error('[Error] Cart Update Failed:', err));
}

// 4. Unit Controls (Preserved for Wholesale Mode)
function insertUnitControls(btn, productId, tier, price, units, size) {
    btn.style.display = 'none';
    // Use the unique wrapper for this product
    let container = document.getElementById('quote-action-' + productId);
    if (!container) {
        container = btn.closest('.product-card') || btn.closest('.product-container') || btn.parentElement;
    }
    let existingControls = container.querySelector('.qty-controls');
    if (existingControls) existingControls.remove();

    let controls = document.createElement('div');
    controls.className = 'qty-controls flex items-center gap-2 mt-2';

    const safeId = 'u' + String(productId).replace(/[^a-zA-Z0-9_-]/g, '');
    const safeSize = (size || '').replace(/[^a-zA-Z0-9_-]/g, '');

    controls.innerHTML = `
        <button class='rose-btn px-3 py-1 rounded-lg minus-btn'>-</button>
        <span id='units-${safeId}-${safeSize}' class="font-bold">${units}</span>
        <button class='rose-btn px-3 py-1 rounded-lg plus-btn'>+</button>
    `;
    container.appendChild(controls);

    controls.querySelector('.minus-btn').onclick = () => updateUnits(productId, tier, units - 1, container, btn, price, size);
    controls.querySelector('.plus-btn').onclick = () => updateUnits(productId, tier, units + 1, container, btn, price, size);
}

function updateUnits(productId, tier, units, parent, btn, price, size) {
    fetch('/update-cart', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: productId, qty: units, tier: tier, price: price, size: size })
    })
    .then(res => res.json())
    .then(data => {
        if (units <= 0) {
            const controls = parent.querySelector('.qty-controls');
            if (controls) controls.remove();
            btn.style.display = '';
        } else {
            const safeId = 'u' + String(productId).replace(/[^a-zA-Z0-9_-]/g, '');
            const safeSize = (size || '').replace(/[^a-zA-Z0-9_-]/g, '');
            const counter = parent.querySelector(`#units-${safeId}-${safeSize}`);
            if (counter) counter.innerText = (typeof data.new_qty !== 'undefined') ? data.new_qty : units;
        }
        updateCartUI(data.new_total);
        if (window.location.pathname.includes('checkout')) location.reload();
    });
}

// Ensure updateUnits is globally accessible for dynamic controls
window.updateUnits = updateUnits;
}

// 5. Global Event Listener
document.addEventListener('click', function(e) {
    // Check for any "Add to Cart" or "Add to Quote" button
    if (e.target.classList.contains('add-to-cart-btn') || e.target.classList.contains('add-to-quote-btn')) {
        addToCart(e.target);
    }
});

// 6. Sync Button States on Load
function syncButtonStates() {
    if (getSiteMode() === 'retail') return; // Skip complex sync for simple retail UI

    fetch('/cart')
        .then(res => res.json())
        .then(cart => {
            document.querySelectorAll('.product-card').forEach(card => {
                const btn = card.querySelector('.add-to-quote-btn');
                if (!btn) return;
                const productId = btn.getAttribute('data-product-id');
                
                for (const key in cart) {
                    const item = cart[key];
                    if (item.product_id == productId && item.qty > 0) {
                        insertUnitControls(btn, productId, item.tier, item.price, item.qty, item.size);
                    }
                }
            });
        });
}

document.addEventListener('DOMContentLoaded', syncButtonStates);