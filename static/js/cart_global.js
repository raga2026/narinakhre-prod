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

document.addEventListener('click', function(e) {
    if (e.target.classList.contains('add-to-quote-btn')) {
        const btn = e.target;
        const sku = btn.getAttribute('data-sku');
        let container = btn.closest('.product-card');
        if (!container) {
            container = btn.closest('.product-info') || btn.closest('.product-container') || btn.parentElement;
        }
        const tierSelect = container.querySelector('.tier-select') || document.getElementById('tier-select-' + sku);
        const tier = tierSelect ? tierSelect.value : btn.getAttribute('data-tier') || 1;
        const price = tierSelect ? tierSelect.options[tierSelect.selectedIndex].getAttribute('data-price') : btn.getAttribute('data-price');
        const size = container.querySelector('.size-select') ? container.querySelector('.size-select').value : '';
        const payload = {
            product_id: sku,
            qty: 1,
            tier: tier,
            price: price,
            size: size
        };
        console.log('[Add to Quote] Sending:', payload);
        fetch('/update-cart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(res => res.json())
        .then(data => {
            console.log('[Add to Quote] Response:', data);
            insertUnitControls(btn, sku, tier, price, 1);
            updateCartUI(data.new_total);
        })
        .catch(err => {
            console.error('[Add to Quote] Error:', err);
        });
    }
});

            if (!container) {
                // fallback for product page
                container = btn.closest('.product-info') || btn.closest('.product-container') || btn.parentElement;
            }


function syncButtonStates() {
    // On page load, check session cart and show controls for added items
    fetch('/cart', { method: 'GET' })
        .then(res => res.text())
        .then(html => {
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');
            const items = Array.from(doc.querySelectorAll('[data-sku]'));
            items.forEach(item => {
                const sku = item.getAttribute('data-sku');
                const tier = item.getAttribute('data-tier') || 1;
                const units = item.getAttribute('data-units') || 1;
                // Try both .product-card and .product-info containers
                let btn = document.querySelector(`.product-card .add-to-quote-btn[data-sku='${sku}']`);
                if (!btn) {
                    btn = document.querySelector(`.product-info .add-to-quote-btn[data-sku='${sku}']`);
                }
                if (!btn) {
                    btn = document.querySelector(`.add-to-quote-btn[data-sku='${sku}']`);
                }
                if (btn) {
                    insertUnitControls(btn, sku, tier, '', units);
                }
            });
        });
}

window.onload = syncButtonStates;
